#!/usr/bin/env python3

"""ontap_check - Check script for Nagios/Icinga"""

import nagiosplugin
from netapp_ontap.host_connection import HostConnection
from netapp_ontap.resources import Aggregate, ClusterPeer, Node, Disk as ODisk, FcInterface, SnapmirrorRelationship, Volume as OVolume, Metrocluster, MetroclusterSvm, MetroclusterDiagnostics, MetroclusterOperation, QuotaReport, IpInterface, Shelf
from netapp_ontap.error import NetAppRestError
import isodate
from datetime import timedelta
import argparse
import re, sys
from pprint import pprint as pprint

# disable warnings from the urllib module
import warnings
warnings.simplefilter("ignore")

class AdvancedScalarContext(nagiosplugin.ScalarContext):
  """Class for defining ScalarContext based on dicts"""
  def evaluate(self, metric, resource):
    if isinstance(metric.value, dict):
      if metric.name == 'ignore_missing':
        return self.result_cls(nagiosplugin.Ok, None, metric)
      elif metric.value['state'] in metric.value['ok_condition']:
        return self.result_cls(nagiosplugin.Ok, None, metric)
      else:
        return self.result_cls(nagiosplugin.Critical, None, metric)
    else:
      return super().evaluate(metric, resource)

class AdvancedSummary(nagiosplugin.Summary):
  """Class for defining advanced summaries based on dicts"""
  def ok(self, results):
    metric_names = []
    for result in results:
      if result.metric.name == 'ignore_missing':
        return f'no {result.metric.value["name"]} available'

      metric_names.append(result.metric.name)

    return f'Check is OK for %s' % ", ".join(metric_names)

class ONTAPResource(nagiosplugin.Resource):
  def __init__(self, hostname, username, password, verify) -> None:
    super().__init__()
    self.hostname = hostname
    self.username = username
    self.password = password
    self.verify = verify

class Aggr(ONTAPResource):
  """aggr - Check aggregate real space usage"""
  def __init__(self, hostname, username, password, verify, regexp, aggregate) -> None:
    super().__init__(hostname, username, password, verify)
    self.regexp = regexp
    self.aggregate = aggregate

  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      aggregates = Aggregate.get_collection(fields='space')
      for aggregate in aggregates:
        if (self.regexp != '' and re.search(self.regexp, aggregate.name)) or (self.aggregate != '' and self.aggregate == aggregate.name):
          yield nagiosplugin.Metric(f'{aggregate.name}', aggregate.space.block_storage.used_percent, '%', context='aggr')

class AggrSummary(nagiosplugin.Summary):

  def ok(self, results):
    for result in results:
      print(result)
    return f'Aggreate size '

class Clusterlinks(ONTAPResource):
  """clusterlinks - check HA-interconnect and cluster links"""
  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      cluster = ClusterPeer()
      peers = cluster.get_collection(fields='status')
      for peer in peers:
        yield nagiosplugin.Metric(f'{peer.name}', { 'state': peer.status.state, 'ok_condition': ['available'] }, context='clusterlinks')

class Global(ONTAPResource):
  """global - check powersupplies, fans, nvram status, temp or global health"""
  def __init__(self, hostname, username, password, verify, plugin) -> None:
    super().__init__(hostname, username, password, verify)
    self.plugin = plugin

  def power(self):
    psu_failed = {}
    for node in self.nodes:
      psu_failed.update({ node['name']: node['controller']['failed_power_supply']['count'] })
    return psu_failed

  def fan(self):
    fan_failed = {}
    for node in self.nodes:
      fan_failed.update({ node['name']: node['controller']['failed_fan']['count'] })
    return fan_failed

  def nvram(self):
    battery_failed = {}
    for node in self.nodes:
      battery_failed.update({ node['name']: { 'state': node['nvram']['battery_state'], 'ok_condition': ['battery_ok'] }})
    return battery_failed

  def temp(self):
    temp_failed = {}
    for node in self.nodes:
      temp_failed.update({ node['name']: { 'state': node['controller']['over_temperature'], 'ok_condition': ['normal'] }})
    return temp_failed

  def health(self):
    node_failed = {}
    for node in self.nodes:
      node_failed.update({ node['name']: { 'state': node['state'], 'ok_condition': ['up', 'booting'] }})
    return node_failed

  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      self.nodes = list(Node.get_collection())
      for node in self.nodes:
        node.get(fields='controller.over_temperature,controller.failed_fan,controller.failed_power_supply,nvram,state')
    result = eval(f'self.{self.plugin}()')
    for node,value in result.items():
      yield nagiosplugin.Metric(f'{self.plugin} - {node}', value, context=self.plugin)

class Disk(ONTAPResource):
  """disk - check netapp system disk state"""
  def probe(self):
    spare = 0
    rebuilding = 0
    aggregate = 0
    failed = 0
    failed_disks = []
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      disks = list(ODisk.get_collection(fields='container_type,state'))
      diskcount = len(disks)
      for disk in disks:
        if disk['container_type'] in ['shared', 'aggregate']:
          if disk['state'] == 'reconstructing':
            rebuilding += 1
          else:
            if disk['state'] == 'removed':
              rebuilding += 1
            else:
              aggregate += 1
        elif disk['container_type'] == 'spare':
          spare += 1
        else:
          if disk['state'] == 'broken' and disk['container_type'] == 'maintenance':
            failed += 1
            failed_disks.append(disk.name)
          else:
            aggregate += 1
    return [nagiosplugin.Metric(f'spare disks', spare, context='disk'),
            nagiosplugin.Metric(f'rebuilding disks', rebuilding, context='disk'),
            nagiosplugin.Metric(f'aggregate disks', aggregate, context='disk'),
            nagiosplugin.Metric(f'failed disks{" " + failed_disks if len(failed_disks) != 0 else ""}', failed, context='disk'),
            nagiosplugin.Metric(f'diskcount', diskcount, context='disk')]

class DiskScalarContext(nagiosplugin.ScalarContext):
  def __init__(self, name, warning=None, critical=None, fmt_metric='{name} is {valueunit}', result_cls=nagiosplugin.Result, diskcount=None):
    super().__init__(name, warning, critical, fmt_metric, result_cls)
    self.diskcount = nagiosplugin.Range(f'{diskcount}:{diskcount}')

  def evaluate(self, metric, resource):
    if 'diskcount' in metric.name:
      self.warning = nagiosplugin.Range(None)
      self.critical = self.diskcount
    if not 'failed' in metric.name:
      self.warning = nagiosplugin.Range(None)
      self.critical = nagiosplugin.Range(None)
    return super().evaluate(metric, resource)

class Multipath(ONTAPResource):
  """multipath - check if all disks are multipathed (4 paths)"""
  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      metrocluster = Metrocluster()
      metrocluster.get(fields='configuration_type,local')
      disks = ODisk.get_collection(fields='container_type,name,node,paths,shelf')
      number_of_paths = 4
      if metrocluster.local.configuration_state == 'configured':
        if metrocluster.configuration_type == 'stretch':
          number_of_paths = 2
        elif metrocluster.configuration_type in ['fabric', 'ip_fabric']:
          number_of_paths = 8
      for disk in disks:
        disk.shelf.get(fields='module_type')
        if disk.shelf.module_type == 'iom12f':
          number_of_paths = 8
        elif disk.shelf.module_type == 'psm3e':
          number_of_paths = 2
        if disk.container_type != 'unknown':
          yield nagiosplugin.Metric(f'disk {disk.node.name}/{disk.name} multipath', {'state': len(disk.paths), 'ok_condition': [number_of_paths]}, context='multipath')

class Fcp(ONTAPResource):
  """fcp - check fcp interfaces"""
  def __init__(self, hostname, username, password, verify, ignore_missing) -> None:
    super().__init__(hostname, username, password, verify)
    self.ignore_missing = ignore_missing

  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      fc_interfaces = FcInterface.get_collection()
      fc_interface_count = 0

      for interface in fc_interfaces:
        fc_interface_count += 1
        interface.get(fields='statistics,metric')
        yield nagiosplugin.Metric(f'{interface.name}', { 'state': interface.statistics.status, 'ok_condition': ['ok'] }, context='fcp')

      if fc_interface_count == 0 and self.ignore_missing:
        yield nagiosplugin.Metric('ignore_missing', {'name': 'FCP'}, context='fcp')

class Interface_Health(ONTAPResource):
  """interface_health - check interface status, home-node and home-port"""
  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      interfaces = IpInterface.get_collection(fields='enabled,scope,state,statistics.status,svm,location')
      for interface in interfaces:
        if interface.scope == 'svm':
          output_description = f'svm {interface.svm.name} lif {interface.name}'
        else:
          output_description = f'cluster interface {interface.name}'
        yield nagiosplugin.Metric(f'{output_description} status', {'state': interface.statistics.status, 'ok_condition': ['ok']}, context='interface_health')
        yield nagiosplugin.Metric(f'{output_description} node', {'state': interface.location.node.name, 'ok_condition': [interface.location.home_node.name]}, context='interface_health')
        yield nagiosplugin.Metric(f'{output_description} port', {'state': interface.location.port.name, 'ok_condition': [interface.location.home_port.name]}, context='interface_health')

class Port_Health(ONTAPResource):
  """port_health - check if port is enabled and up"""
  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      interfaces = IpInterface.get_collection(fields='enabled,scope,state,statistics.status,svm')
      for interface in interfaces:
        if interface.scope == 'svm':
          output_description = f'lif {interface.svm.name} {interface.name}'
        else:
          output_description = f'cluster interface {interface.name}'
        yield nagiosplugin.Metric(f'{output_description} enabled', {'state': interface.enabled, 'ok_condition': [True]}, context='port_health')
        yield nagiosplugin.Metric(f'{output_description} state', {'state': interface.state, 'ok_condition': ['up']}, context='port_health')

class Snapmirror(ONTAPResource):
  """snapmirror - check snapmirror healthness"""
  def __init__(self, hostname, username, password, verify, volume, vserver, exclude, regexp, ignore_missing) -> None:
    super().__init__(hostname, username, password, verify)
    self.volume = volume
    self.vserver = vserver
    self.exclude = exclude.split(',')
    self.regexp = regexp
    self.ignore_missing = ignore_missing

  def snapmirror_state(self, snapmirror):
    if not snapmirror.healthy:
      state = { 'state': f'{snapmirror.state} - {snapmirror.unhealthy_reason[0].message}', 'ok_condition': ['snapmirrored'] }
    else:
      state = { 'state': snapmirror.state, 'ok_condition': ['snapmirrored'] }
    return nagiosplugin.Metric(f'{snapmirror.destination.path}', state, context='snapmirror')

  def snapmirror_lag(self, snapmirror):
    return nagiosplugin.Metric(f'{snapmirror.destination.path} lag time', isodate.parse_duration(snapmirror['lag_time']).total_seconds(), context='snapmirror')

  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      snapmirrors = SnapmirrorRelationship.get_collection(fields='healthy,unhealthy_reason,lag_time,state,destination')
      snapmirror_count = 0

      for snapmirror in snapmirrors:
        snapmirror_count += 1
        if (self.volume != 'all' and self.volume == snapmirror.destination.path.split(':')[1]) or (self.vserver != 'all' and self.vserver == snapmirror.destination.svm.name) or \
           (self.exclude != [''] and snapmirror.destination.path.split(':')[1] not in self.exclude and not self.regexp):
          yield self.snapmirror_state(snapmirror)
          if snapmirror.state == 'snapmirrored':
            yield self.snapmirror_lag(snapmirror)
        elif self.regexp:
          for exclude in self.exclude:
            if not re.search(exclude, snapmirror.destination.path.split(':')[1]):
              yield self.snapmirror(snapmirror)
              if snapmirror.state == 'snapmirrored':
                yield self.snapmirror_lag(snapmirror)
        elif self.volume == 'all' and self.vserver == 'all':
          yield self.snapmirror_state(snapmirror)
          if snapmirror.state == 'snapmirrored':
            yield self.snapmirror_lag(snapmirror)

      if snapmirror_count == 0 and self.ignore_missing:
        yield nagiosplugin.Metric('ignore_missing', {'name': 'Snapmirror'}, context='snapmirror')

class SnapmirrorScalarContext(AdvancedScalarContext):
  def __init__(self, name, warning=None, critical=None, fmt_metric='{name} is {valueunit}', result_cls=nagiosplugin.Result, lag=None):
    super().__init__(name, warning, critical, fmt_metric, result_cls)
    lag_time = timedelta(seconds=lag)
    self.lag = nagiosplugin.Range(lag_time.total_seconds())

  def evaluate(self, metric, resource):
    if 'lag' in metric.name:
      self.warning = nagiosplugin.Range(None)
      self.critical = self.lag
    else:
      self.warning = nagiosplugin.Range(None)
      self.critical = nagiosplugin.Range(None)
    return super().evaluate(metric, resource)

class Sparedisks(ONTAPResource):
  """sparedisks - check netapp system spare disks"""
  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      nodes = Node.get_collection(fields='is_spares_low')

      for node in nodes:
        yield nagiosplugin.Metric(f'{node.name}', int(node["is_spares_low"]), context='sparedisks')

class SparedisksSummary(nagiosplugin.Summary):
  """Sparedisk Summary - Return result in a more user friendly way"""
  def ok(self, results):
    nodes = []
    for result in results:
      if result.state.code != 0:
        continue
      nodes.append(result.metric.name)
    return "Result OK for %s" % ", ".join(nodes)

  def problem(self, results):
    nodes = []
    for result in results:
      if result.state.code == 0:
        continue
      nodes.append(result.metric.name)
    return "Result NOT OK for %s" % ", ".join(nodes)


class Volume(ONTAPResource):
  """volume - check volume usage"""
  def __init__(self, hostname, username, password, verify, ignore, volume, volumelist, vserver, regexp, exclude) -> None:
    super().__init__(hostname, username, password, verify)
    self.ignore = ignore
    self.volume = volume
    self.volumelist = volumelist.split(',')
    self.vserver = vserver
    self.regexp = regexp
    self.exclude = exclude.split(',')

  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      volumes = OVolume.get_collection(fields='svm,state,space.percent_used,space.physical_used_percent,space.snapshot,files')
      for volume in volumes:
        if volume.state != 'online':
          continue

        if self.volume != '' and volume.name != self.volume:
          continue

        if self.volumelist != [''] and volume.name not in self.volumelist:
          continue

        if self.vserver != '' and volume.svm.name != self.vserver:
          continue

        if self.regexp != '' and not re.search(self.regexp, volume.name):
          continue

        if self.exclude != [''] and volume.name in self.exclude:
          continue

        yield nagiosplugin.Metric(f'{volume.name} space', volume.space.percent_used, '%', context='volume')
        yield nagiosplugin.Metric(f'{volume.name} inodes', int(volume.files.used / volume.files.maximum), '%', context='volume')
        if not self.ignore:
          yield nagiosplugin.Metric(f'{volume.name} snap', volume.space.snapshot.space_used_percent, '%', context='volume')

class VolumeScalarContext(nagiosplugin.ScalarContext):
  def __init__(self, name, warning=None, critical=None, fmt_metric='{name} is {valueunit}', result_cls=nagiosplugin.Result, inode_warning=None, inode_critical=None, snap_warning=None, snap_critical=None):
    super().__init__(name, fmt_metric=fmt_metric, result_cls=result_cls)
    self.size_warning = nagiosplugin.Range(warning)
    self.size_critical = nagiosplugin.Range(critical)
    self.inode_warning = nagiosplugin.Range(inode_warning)
    self.inode_critical = nagiosplugin.Range(inode_critical)
    self.snap_warning = nagiosplugin.Range(snap_warning)
    self.snap_critical = nagiosplugin.Range(snap_critical)

  def evaluate(self, metric, resource):
    if 'space' in metric.name:
      self.warning = self.size_warning
      self.critical = self.size_critical
    elif 'inodes' in metric.name:
      self.warning = self.inode_warning
      self.critical = self.inode_critical
    elif 'snap' in metric.name:
      self.warning = self.snap_warning
      self.critical = self.snap_critical
    return super().evaluate(metric, resource)

class Metrocluster_State(ONTAPResource):
  """metrocluster_state - check metrocluster state"""
  def __init__(self, hostname, username, password, verify, ignore_missing) -> None:
    super().__init__(hostname, username, password, verify)
    self.ignore_missing = ignore_missing

  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      metrocluster = Metrocluster()
      metrocluster.get(fields='local,local.mode,local.periodic_check_enabled,remote,remote.mode,remote.periodic_check_enabled')

      if metrocluster.local.configuration_state == 'not_configured' and self.ignore_missing:
        return nagiosplugin.Metric('ignore_missing', {'name': 'MetroCluster'}, context='metrocluster_state')

      return [nagiosplugin.Metric('metrocluster local state', {'state': metrocluster.local.configuration_state, 'ok_condition': ['configured']}, context='metrocluster_state'),
              nagiosplugin.Metric('metrocluster local mode', {'state': metrocluster.local.mode, 'ok_condition': ['normal']}, context='metrocluster_state'),
              nagiosplugin.Metric('metrocluster local periodic check', {'state': metrocluster.local.periodic_check_enabled, 'ok_condition': [True]}, context='metrocluster_state'),
              nagiosplugin.Metric('metrocluster remote partner reachable', {'state': metrocluster.local.partner_cluster_reachable, 'ok_condition': [True]}, context='metrocluster_state'),
              nagiosplugin.Metric('metrocluster remote state', {'state': metrocluster.remote.configuration_state, 'ok_condition': ['configured']}, context='metrocluster_state'),
              nagiosplugin.Metric('metrocluster remote mode', {'state': metrocluster.remote.mode, 'ok_condition': ['normal']}, context='metrocluster_state'),
              nagiosplugin.Metric('metrocluster remote periodic check', {'state': metrocluster.remote.periodic_check_enabled, 'ok_condition': [True]}, context='metrocluster_state')]

class Metrocluster_Config(ONTAPResource):
  """metrocluster_config - check metrocluster config replication"""
  def __init__(self, hostname, username, password, verify, ignore_missing) -> None:
    super().__init__(hostname, username, password, verify)
    self.ignore_missing = ignore_missing

  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      try:
        metrocluster_svms = MetroclusterSvm.get_collection(fields='configuration_state,svm.name')
        metrocluster_diagnostics = MetroclusterDiagnostics()
        metrocluster_diagnostics.get(fields='config_replication.state')
      except NetAppRestError as e:
        if (
          self.ignore_missing and
          e.status_code == 500 and
          'MetroCluster is not configured' in e.response_body['error']['message']
        ):
          yield nagiosplugin.Metric('ignore_missing', {'name': 'MetroCluster'}, context='metrocluster_config')
          return
        raise

      yield nagiosplugin.Metric('config replication', {'state': metrocluster_diagnostics.config_replication['state'], 'ok_condition': ['ok']}, context='metrocluster_config')
      for metrocluster_svm in metrocluster_svms:
        yield nagiosplugin.Metric(f'svm {metrocluster_svm.svm.name} config state', {'state': metrocluster_svm.configuration_state, 'ok_condition': ['healthy']}, context='metrocluster_config')

class Metrocluster_Check(ONTAPResource):
  """metrocluster_check - netapp mcc metrocluster check"""
  def __init__(self, hostname, username, password, verify, ignore_missing) -> None:
    super().__init__(hostname, username, password, verify)
    self.ignore_missing = ignore_missing

  def probe(self):
    fields = ['aggregate', 'cluster', 'config_replication', 'connection', 'interface', 'node', 'volume']
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      try:
        metrocluster_diagnostics = MetroclusterDiagnostics()
        metrocluster_diagnostics.get(fields='.state,'.join(fields))
      except NetAppRestError as e:
        if (
          self.ignore_missing and
          e.status_code == 500 and
          'MetroCluster is not configured' in e.response_body['error']['message']
        ):
          yield nagiosplugin.Metric('ignore_missing', {'name': 'MetroCluster'}, context='metrocluster_check')
          return
        raise

      for field in fields:
        yield nagiosplugin.Metric(f'component {field}', {'state': metrocluster_diagnostics[field]['state'], 'ok_condition': ['ok']}, context='metrocluster_check')

class Metrocluster_Aggr(ONTAPResource):
  """metrocluster_aggr - check metrocluster aggregate state"""
  def __init__(self, hostname, username, password, verify, ignore_missing) -> None:
    super().__init__(hostname, username, password, verify)
    self.ignore_missing = ignore_missing

  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      try:
        metrocluster_diagnostics = MetroclusterDiagnostics()
        metrocluster_diagnostics.get(fields='aggregate')
      except NetAppRestError as e:
        if (
          self.ignore_missing and
          e.status_code == 500 and
          'MetroCluster is not configured' in e.response_body['error']['message']
        ):
          yield nagiosplugin.Metric('ignore_missing', {'name': 'MetroCluster'}, context='metrocluster_aggr')
          return
        raise

      for detail in metrocluster_diagnostics.aggregate.details:
        for check in detail.checks:
          yield nagiosplugin.Metric(f'metrocluster aggregate {detail.aggregate.name} - {check.name}', {'state': check.result, 'ok_condition': ['ok']}, context='metrocluster_aggr')

class Quota(ONTAPResource):
  """quota - check quota usage"""
  def __init__(self, hostname, username, password, verify, volume, target, vserver) -> None:
    super().__init__(hostname, username, password, verify)
    self.volume = volume
    self.target = target
    self.vserver = vserver

  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      quotas = QuotaReport.get_collection(fields='files,files.hard_limit,space,space.hard_limit,qtree,svm,volume,type,users')
      for quota in quotas:
        try:
          files_hard_limit = quota.files.hard_limit
        except AttributeError as e:
          files_hard_limit = 0
        try:
          space_hard_limit = quota.space.hard_limit
        except AttributeError as e:
          space_hard_limit = 0
        try:
          space_used_hard_limit_percent = quota.space.used.hard_limit_percent
        except AttributeError as e:
          space_used_hard_limit_percent = 0
        if (self.volume != '' and quota.volume.name != self.volume) or (self.vserver != '' and quota.svm.name != self.vserver):
          continue
        else:
          if quota.type == 'user' and self.target == '':
            for user in quota.users:
              if user.name not in ['', '*']:
                yield nagiosplugin.Metric(f'quota {quota.volume.name}/{user.name}: {space_hard_limit} {quota.space.used.total} {files_hard_limit} {quota.files.used.total}', space_used_hard_limit_percent, '%', context='quota')
          elif quota.type == 'tree':
            if self.target != '' and quota.qtree.name != self.target:
              continue
            else:
              if quota.qtree.name not in ['', '*']:
                yield nagiosplugin.Metric(f'quota {quota.volume.name}/{quota.qtree.name}: {space_hard_limit} {quota.space.used.total} {files_hard_limit} {quota.files.used.total}', space_used_hard_limit_percent, '%', context='quota')

class Volume_Health(ONTAPResource):
  """volume_health - check volume health"""
  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      volumes = OVolume.get_collection(fields='state')
      for volume in volumes:
        yield nagiosplugin.Metric(f'{volume.name}', {'state': volume.state, 'ok_condition': ['online']}, context='volume_health')

class Node_Health(ONTAPResource):
  """node_health - check node health"""
  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      nodes = Node.get_collection(fields='statistics')
      for node in nodes:
        yield nagiosplugin.Metric(f'{node.name}', {'state': node.statistics.status, 'ok_condition': ['ok']}, context='node_health')

class Node_Cpu(ONTAPResource):
  """node_cpu - node cpu utilization"""
  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      nodes = Node.get_collection(fields='statistics')
      for node in nodes:
        node.get(fields='statistics')
        yield nagiosplugin.Metric(f'{node.name}', node.statistics.processor_utilization_raw / node.statistics.processor_utilization_base * 100, '%', context='node_cpu')

#Node Memory utilization
#Node virtualization problem


@nagiosplugin.guarded
def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('-H', '--hostname')
  parser.add_argument('-U', '--username')
  parser.add_argument('-p', '--password')
  parser.add_argument('-i', '--insecure', action='store_false', default=True, help='disable ssl certificate check')
  parser.add_argument('-v', '--verbose', action='count', default=0,
                    help='increase output verbosity (use up to 3 times)')
  parser.add_argument('--ignore-missing', action='store_true', default=False, help='Ignore entities which do not exist')
  subparsers = parser.add_subparsers(dest='check')
  # check aggr
  subparser = subparsers.add_parser('aggr', description="aggr - check aggregate real space usage")
  subparser.add_argument('-w', '--warning', metavar='RANGE', default='',
                    help='return warning if load is outside RANGE')
  subparser.add_argument('-c', '--critical', metavar='RANGE', default='',
                    help='return critical if load is outside RANGE')
  subparser.add_argument('-r', '--regexp', default='',
                    help='regex matching the name of the aggregate')
  subparser.add_argument('-A', '--aggr', default='',
                    help='aggregate name')
  # check clusterlinks
  subparser = subparsers.add_parser('clusterlinks', description="clusterlinks - check HA-interconnect and cluster links")
  # check global
  subparser = subparsers.add_parser('global', description="global - check power supplies, fans, nvram status, temp or global health")
  subparser.add_argument('--plugin', default='',
                    help='plugin choices are power, fan, nvram, temp, health')
  # check disk
  subparser = subparsers.add_parser('disk', description="disk - check netapp system disk state")
  subparser.add_argument('-w', '--warning', metavar='RANGE', default='0',
                    help='return warning if load is outside RANGE')
  subparser.add_argument('-c', '--critical', metavar='RANGE', default='',
                    help='return critical if load is outside RANGE')
  subparser.add_argument('-d', '--disks', default='',
                    help='number of expected disks')
  # check multipath
  subparser = subparsers.add_parser('multipath', description="multipath - check if all disks are multipathed (4 paths)")
  # check fcp
  subparser = subparsers.add_parser('fcp', description="fcp - check fcp interfaces")
  # check interface_health
  subparser = subparsers.add_parser('interface_health', description="interface_health - check interface status, home-node and home-port")
  # check port_health
  subparser = subparsers.add_parser('port_health', description="port_health - check if port is enabled and up")
  # check snapmirror
  subparser = subparsers.add_parser('snapmirror', description="snapmirror - check snapmirror healthness")
  subparser.add_argument('--lag', default='100800', type=int,
                    help='delay in seconds, default 28h')
  subparser.add_argument('--volume', default='all',
                    help='name of the destination volume')
  subparser.add_argument('--vserver', default='all',
                    help='name of the destination svm')
  subparser.add_argument('--exclude', default='',
                    help='list of volume names to be excluded')
  subparser.add_argument('--regexp', action='store_true', default=False,
                    help='enable regexp matching for the exclusion list')
  # check sparedisks
  subparser = subparsers.add_parser('sparedisks', description="sparedisks - check netapp system spare disks")
  # check volume
  subparser = subparsers.add_parser('volume', description="volume - check volume usage")
  subparser.add_argument('-w', '--size-warning', dest='size_warning', metavar='RANGE', default='',
                    help='return warning if space is outside RANGE')
  subparser.add_argument('-c', '--size-critical', dest='size_critical', metavar='RANGE', default='',
                    help='return critical if space is outside RANGE')
  subparser.add_argument('--inode-warning', dest='inode_warning', metavar='RANGE', default='',
                    help='return warning if inode is outside RANGE')
  subparser.add_argument('--inode-critical', dest='inode_critical', metavar='RANGE', default='',
                    help='return critical if inode is outside RANGE')
  subparser.add_argument('--snap-warning', dest='snap_warning', metavar='RANGE', default='',
                    help='return warning if snap is outside RANGE')
  subparser.add_argument('--snap-critical', dest='snap_critical', metavar='RANGE', default='',
                    help='return critical if snap is outside RANGE')
  subparser.add_argument('--snap-ignore', dest='snap_ignore', metavar='store_true', default=False,
                    help='ignore snap')
  subparser.add_argument('-V', '--volume', default='',
                    help='select volume')
  subparser.add_argument('--volumelist', default='',
                    help='select volume list')
  subparser.add_argument('--vserver', default='',
                    help='select vserver/svm')
  subparser.add_argument('--regexp', default='',
                    help='regex matching the name of the volume')
  subparser.add_argument('--exclude', default='',
                    help='exclude volume list')
  # check metrocluster_state
  subparser = subparsers.add_parser('metrocluster_state', description="metrocluster_state - check metrocluster state")
  # check metrocluster_config
  subparser = subparsers.add_parser('metrocluster_config', description="metrocluster_config - check metrocluster config replication")
  # check metrocluster_check
  subparser = subparsers.add_parser('metrocluster_check', description="metrocluster_check - netapp mcc metrocluster check")
  # check metrocluster_aggr
  subparser = subparsers.add_parser('metrocluster_aggr', description="metrocluster_aggr - check metrocluster aggregate state")
  # check quota
  subparser = subparsers.add_parser('quota', description="quota - check quota usage")
  subparser.add_argument('-w', '--warning', metavar='RANGE', default='0',
                    help='warning threshold for used space in percent')
  subparser.add_argument('-c', '--critical', metavar='RANGE', default='',
                    help='critical threshold for used space')
  subparser.add_argument('-V', '--volume', default='',
                    help='name of the volume to check the quotas on')
  subparser.add_argument('-t', '--target', default='',
                    help='name of the qtree to check the quota')
  subparser.add_argument('--vserver', default='',
                    help='name of the svm to check quotas on')
  # check volume_health
  subparser = subparsers.add_parser('volume_health', description="volume_health - check volume health")
  # check node_health
  subparser = subparsers.add_parser('node_health', description="node_health - check node health")
  # check node_cpu
  subparser = subparsers.add_parser('node_cpu', description="node_cpu - node cpu utilization")
  subparser.add_argument('-w', '--warning', metavar='RANGE', default='',
                    help='return warning if cpu usage in percent is outside RANGE')
  subparser.add_argument('-c', '--critical', metavar='RANGE', default='',
                    help='return critical if cpu usage in percent is outside RANGE')
  args = parser.parse_args()

  if args.check == 'aggr':
    if len(args.regexp) == 0:
      args.regexp = '.*'

    check = nagiosplugin.Check(
        Aggr(args.hostname, args.username, args.password, args.insecure, args.regexp, args.aggr),
        nagiosplugin.ScalarContext(args.check, args.warning, args.critical)) #, AggrSummary())
  elif args.check == 'clusterlinks':
    check = nagiosplugin.Check(
        Clusterlinks(args.hostname, args.username, args.password, args.insecure),
        AdvancedScalarContext(args.check)) #, AggrSummary())
  elif args.check == 'global':
    check = nagiosplugin.Check(
        Global(args.hostname, args.username, args.password, args.insecure, args.plugin),
        AdvancedScalarContext(args.plugin, fmt_metric=f'{args.plugin} is {{value}}')) #, AggrSummary())
  elif args.check == 'disk':
    check = nagiosplugin.Check(
        Disk(args.hostname, args.username, args.password, args.insecure),
        DiskScalarContext(args.check, args.warning, args.critical, diskcount=args.disks)) #, AggrSummary())
  elif args.check == 'multipath':
    check = nagiosplugin.Check(
        Multipath(args.hostname, args.username, args.password, args.insecure),
        AdvancedScalarContext(args.check)) #, AggrSummary())
  elif args.check == 'fcp':
    check = nagiosplugin.Check(
        Fcp(args.hostname, args.username, args.password, args.insecure, args.ignore_missing),
        AdvancedScalarContext(args.check),
        AdvancedSummary())
  elif args.check == 'interface_health':
    check = nagiosplugin.Check(
        Interface_Health(args.hostname, args.username, args.password, args.insecure),
        AdvancedScalarContext(args.check)) #, AggrSummary())
  elif args.check == 'port_health':
    check = nagiosplugin.Check(
        Port_Health(args.hostname, args.username, args.password, args.insecure),
        AdvancedScalarContext(args.check)) #, AggrSummary())
  elif args.check == 'snapmirror':
    check = nagiosplugin.Check(
        Snapmirror(args.hostname, args.username, args.password, args.insecure, args.volume, args.vserver, args.exclude, args.regexp, args.ignore_missing),
        SnapmirrorScalarContext(args.check, lag=args.lag),
        AdvancedSummary())
  elif args.check == 'sparedisks':
    check = nagiosplugin.Check(
        Sparedisks(args.hostname, args.username, args.password, args.insecure),
        nagiosplugin.ScalarContext(args.check, critical='0:0'),
        SparedisksSummary())
  elif args.check == 'volume':
    check = nagiosplugin.Check(
        Volume(args.hostname, args.username, args.password, args.insecure, args.snap_ignore, args.volume, args.volumelist, args.vserver, args.regexp, args.exclude),
        VolumeScalarContext(args.check, args.size_warning, args.size_critical, inode_warning=args.inode_warning, inode_critical=args.inode_critical, snap_warning=args.snap_warning, snap_critical=args.snap_critical)) #, AggrSummary())
  elif args.check == 'metrocluster_state':
    check = nagiosplugin.Check(
        Metrocluster_State(args.hostname, args.username, args.password, args.insecure, args.ignore_missing),
        AdvancedScalarContext(args.check),
        AdvancedSummary())
  elif args.check == 'metrocluster_config':
    check = nagiosplugin.Check(
        Metrocluster_Config(args.hostname, args.username, args.password, args.insecure, args.ignore_missing),
        AdvancedScalarContext(args.check),
        AdvancedSummary())
  elif args.check == 'metrocluster_check':
    check = nagiosplugin.Check(
        Metrocluster_Check(args.hostname, args.username, args.password, args.insecure, args.ignore_missing),
        AdvancedScalarContext(args.check),
        AdvancedSummary())
  elif args.check == 'metrocluster_aggr':
    check = nagiosplugin.Check(
        Metrocluster_Aggr(args.hostname, args.username, args.password, args.insecure, args.ignore_missing),
        AdvancedScalarContext(args.check),
        AdvancedSummary())
  elif args.check == 'quota':
    check = nagiosplugin.Check(
        Quota(args.hostname, args.username, args.password, args.insecure, args.volume, args.target, args.vserver),
        nagiosplugin.ScalarContext(args.check, args.warning, args.critical)) #, AggrSummary())
  elif args.check == 'volume_health':
    check = nagiosplugin.Check(
        Volume_Health(args.hostname, args.username, args.password, args.insecure),
        AdvancedScalarContext(args.check)) #, AggrSummary())
  elif args.check == 'node_health':
    check = nagiosplugin.Check(
        Node_Health(args.hostname, args.username, args.password, args.insecure),
        AdvancedScalarContext(args.check)) #, AggrSummary())
  elif args.check == 'node_cpu':
    check = nagiosplugin.Check(
        Node_Cpu(args.hostname, args.username, args.password, args.insecure),
        nagiosplugin.ScalarContext(args.check, args.warning, args.critical)) #, AggrSummary())
  else:
    sys.exit('Check does not exist. Use --help')
  check.main(verbose=args.verbose)

if __name__ == '__main__':
  main()