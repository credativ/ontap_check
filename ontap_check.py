#!/usr/bin/env python3

"""ontap_check - Check script for Nagios/Icinga"""

import nagiosplugin
from netapp_ontap.host_connection import HostConnection
from netapp_ontap.resources import Aggregate, ClusterPeer, Node, Disk as ODisk, FcInterface, SnapmirrorRelationship, Volume as OVolume, Metrocluster, QuotaReport
from netapp_ontap.error import NetAppRestError
import argparse
import re, sys
from pprint import pprint as pprint

# disable warnings from the urllib module
import warnings
warnings.simplefilter("ignore")

class AdvancedScalarContext(nagiosplugin.ScalarContext):
  def evaluate(self, metric, resource):
    if isinstance(metric.value, dict):
      if metric.value['state'] in metric.value['ok_condition']:
        return self.result_cls(nagiosplugin.Ok, None, metric)
      else:
        return self.result_cls(nagiosplugin.Critical, None, metric)
    else:
      return super().evaluate(metric, resource)

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
      if node['controller']['failed_power_supply']['count'] == 0:
        psu_failed.update({ node['name']: node['controller']['failed_power_supply']['count'] })
    return psu_failed

  def fan(self):
    fan_failed = {}
    for node in self.nodes:
      if node['controller']['failed_fan']['count'] != 0:
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
    print(result)
    if not len(result):
      result = {"": 0}
    for node,value in result.items():
      yield nagiosplugin.Metric(f'{self.plugin} - {node}', value, context=self.plugin)

class Disk(ONTAPResource):
  """disk - check netapp system disk state"""
  def probe(self):
    spare = 0
    rebuilding = 0
    aggregate_count = 0
    failed = 0
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      disks = ODisk.get_collection()
      diskcount = len(list(disks))
      for disk in disks:
        disk.get()
        if disk['state'] == 'spare':
          print(f'spare: {spare}')
          spare += 1
        elif disk['state'] == 'broken':
          print(f'failed: {failed}')
          failed += 1
        elif disk['state'] == 'reconstructing':
          print(f'rebuilding: {rebuilding}')
          rebuilding += 1
        # elif 'aggregates' in list(disk):
        # # else:
        #   print(disk['aggregates'])
        #   for aggregate in disk['aggregates']:
        #     aggregate.get()
        #     if aggregate['state'] in ['online', 'onlining', 'relocating']:
        #       print(f'aggregate: {aggregate_count}')
        #       aggregate_count += 1
    # print(f'{spare}, {rebuilding}, {failed}, {aggregate_count}')
    return [nagiosplugin.Metric(f'spare disks', spare, context='disk'),
            nagiosplugin.Metric(f'rebuilding disks', rebuilding, context='disk'),
            nagiosplugin.Metric(f'failed disks', failed, context='disk'),
            nagiosplugin.Metric(f'diskcount', diskcount, context='disk')]
            # nagiosplugin.Metric(f'aggregate disks', aggregate_count, context='disk')]

class DiskScalarContext(nagiosplugin.ScalarContext):
  def __init__(self, name, warning=None, critical=None, fmt_metric='{name} is {valueunit}', result_cls=nagiosplugin.Result, diskcount=None):
    super().__init__(name, warning, critical, fmt_metric, result_cls)
    self.diskcount = nagiosplugin.Range(f'{diskcount}:{diskcount}')

  def evaluate(self, metric, resource):
    if 'diskcount' in metric.name:
      self.warning = nagiosplugin.Range(None)
      self.critical = self.diskcount
    return super().evaluate(metric, resource)

class Multipath(ONTAPResource):
  """multipath - check if all disks are multipathed (4 paths)"""
  pass

class Fcp(ONTAPResource):
  """fcp - check fcp interfaces"""
  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      fc_interfaces = FcInterface.get_collection()
      for interface in fc_interfaces:
        interface.get(fields='statistics,metric')
        yield nagiosplugin.Metric(f'{interface.name}', { 'state': interface.statistics.status, 'ok_condition': ['ok'] }, context='fcp')

class Interfaces(ONTAPResource):
  """interfaces - check cdot interface groups status"""
  pass

# ???
#Port Health

class Snapmirror(ONTAPResource):
  """snapmirror - check snapmirror healthness"""
  def __init__(self, hostname, username, password, verify, volume, vserver, exclude, regexp) -> None:
    super().__init__(hostname, username, password, verify)
    self.volume = volume
    self.vserver = vserver
    self.exclude = exclude.split(',')
    self.regexp = regexp

# @todo: How to handle lag_time properly? It is in ISO-8601 duration format
# .      regexp option should enable regexp match for exclude list
  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      snapmirrors = SnapmirrorRelationship.get_collection(fields='healthy,unhealthy_reason,lag_time,state,source')
      for snapmirror in snapmirrors:
        # snapmirror.get()
        # print(snapmirror.healthy)
        print(snapmirror)
        if (self.volume != '' and self.volume == snapmirror.source.path.split(':')[1]) or (self.vserver != '' and self.vserver == snapmirror.source.svm.name) or \
           (self.exclude != [''] and snapmirror.source.path.split(':')[1] not in self.exclude) or (self.regexp != '' and re.search(self.regexp, self.exclude)):
          if not snapmirror.healthy:
            state = { 'state': f'{snapmirror.state} - {snapmirror.unhealthy_reason[0].message}', 'ok_condition': ['snapmirrored'] }
            # print(snapmirror.unhealthy_reason)
          else:
            state = { 'state': snapmirror.state, 'ok_condition': ['snapmirrored'] }
          yield nagiosplugin.Metric(f'{snapmirror.source.path}', state, context='snapmirror')

class SnapmirrorScalarContext(AdvancedScalarContext):
  def __init__(self, name, warning=None, critical=None, fmt_metric='{name} is {valueunit}', result_cls=nagiosplugin.Result, lag=None):
    super().__init__(name, warning, critical, fmt_metric, result_cls)
    self.lag = nagiosplugin.Range(lag)

  def evaluate(self, metric, resource):
    if 'lag' in metric.name:
      self.warning = nagiosplugin.Range(None)
      self.critical = self.lag
    return super().evaluate(metric, resource)

class Sparedisks(ONTAPResource):
  """sparedisks - check netapp system spare disks"""
  def __init__(self, hostname, username, password, verify) -> None:
    super().__init__(hostname, username, password, verify)

  def probe(self):
    spare = 0
    zeroed = 0
    unassigned = 0
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      disks = ODisk.get_collection(fields='container_type,state')
      for disk in disks:
        # disk.get()
        if disk['container_type'] == 'spare':
          spare += 1
          if disk['state'] == 'zeroing':
            zeroed += 1
        elif disk['container_type'] == 'unassigned':
          unassigned += 1
    # print(f'spare: {spare}, zeroed: {zeroed}, unassigned: {unassigned}')
    # return [nagiosplugin.Metric(f'spare disks', spare, context='sparedisks'),
    return [nagiosplugin.Metric(f'zeroing disks', zeroed, context='sparedisks'),
            nagiosplugin.Metric(f'unassigned disks', unassigned, context='sparedisks')]

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
      volumes = OVolume.get_collection(fields='svm,state,space.percent_used,space.physical_used_percent,space.snapshot')
      for volume in volumes:
        if volume.state == 'online' and ((self.volume != '' and volume.name == self.volume) or (self.volumelist != [''] and volume.name in self.volumelist) or \
                                         (self.vserver != '' and volume.svm.name == self.vserver) or (self.regexp != '' and re.search(self.regexp, volume.name)) or \
                                         (self.exclude != [''] and volume.name not in self.exclude) or \
                                         (self.volume == '' and self.volumelist == [''] and self.vserver == '' and self.regexp == '' and self.exclude == [''])):
          yield nagiosplugin.Metric(f'{volume.name} space', volume.space.percent_used, '%', context='volume')
          yield nagiosplugin.Metric(f'{volume.name} physical', volume.space.physical_used_percent, '%', context='volume')
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
    elif 'physical' in metric.name:
      self.warning = self.inode_warning
      self.critical = self.inode_critical
    elif 'snap' in metric.name:
      self.warning = self.snap_warning
      self.critical = self.snap_critical
    return super().evaluate(metric, resource)

class Metrocluster_State(ONTAPResource):
  """metrocluster_state - check metrocluster state"""
  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      metrocluster = Metrocluster()
      metrocluster.get(fields='local,local.mode,local.periodic_check_enabled,remote,remote.mode,remote.periodic_check_enabled')
      return [nagiosplugin.Metric('metrocluster state', {'state': metrocluster.local.configuration_state, 'ok_condition': ['configured']}, context='metrocluster_state'),
              nagiosplugin.Metric('metrocluster mode', {'state': metrocluster.local.mode, 'ok_condition': ['normal']}, context='metrocluster_state'),
              nagiosplugin.Metric('metrocluster periodic check', {'state': metrocluster.local.periodic_check_enabled, 'ok_condition': [True]}, context='metrocluster_state'),
              nagiosplugin.Metric('metrocluster partner reachable', {'state': metrocluster.local.partner_cluster_reachable, 'ok_condition': [True]}, context='metrocluster_state'),
              nagiosplugin.Metric('metrocluster state', {'state': metrocluster.remote.configuration_state, 'ok_condition': ['configured']}, context='metrocluster_state'),
              nagiosplugin.Metric('metrocluster mode', {'state': metrocluster.remote.mode, 'ok_condition': ['normal']}, context='metrocluster_state'),
              nagiosplugin.Metric('metrocluster periodic check', {'state': metrocluster.remote.periodic_check_enabled, 'ok_condition': [True]}, context='metrocluster_state')]

class Metrocluster_Check(ONTAPResource):
  """metrocluster_check - netapp mcc metrocluster check"""
  pass

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

#Volume Health
class Volume_Health(ONTAPResource):
  """volume_health - check volume health"""
  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      volumes = OVolume.get_collection(fields='state')
      for volume in volumes:
        yield nagiosplugin.Metric(f'{volume.name}', {'state': volume.state, 'ok_condition': ['online']}, context='volume_health')

#Node Health
class Node_Health(ONTAPResource):
  """node_health - node health"""
  def probe(self):
    with HostConnection(self.hostname, username=self.username, password=self.password, verify=self.verify):
      nodes = Node.get_collection(fields='statistics')
      for node in nodes:
        yield nagiosplugin.Metric(f'{node.name}', {'state': node.statistics.status, 'ok_condition': ['ok']}, context='node_health')

#Node CPU utilization
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
  parser.add_argument('-i', '--insecure', action='store_false', default=True)
  parser.add_argument('-v', '--verbose', action='count', default=0,
                    help='increase output verbosity (use up to 3 times)')
  subparsers = parser.add_subparsers(help='Aggregate check', dest='check', description="aggr - Check aggregate real space usage")
  # check aggr
  parser_aggr = subparsers.add_parser('aggr')
  parser_aggr.add_argument('-w', '--warning', metavar='RANGE', default='',
                    help='return warning if load is outside RANGE')
  parser_aggr.add_argument('-c', '--critical', metavar='RANGE', default='',
                    help='return critical if load is outside RANGE')
  parser_aggr.add_argument('-r', '--regexp', default='',
                    help='regex matching the name of the aggregate')
  parser_aggr.add_argument('-A', '--aggr', default='',
                    help='aggregate name')
  # check clusterlinks
  parser_aggr = subparsers.add_parser('clusterlinks')
  # check global
  parser_aggr = subparsers.add_parser('global')
  parser_aggr.add_argument('--plugin', default='',
                    help='plugin choices are power, fan, nvram, temp, health')
  # check disk
  parser_aggr = subparsers.add_parser('disk')
  parser_aggr.add_argument('-w', '--warning', metavar='RANGE', default='0',
                    help='return warning if load is outside RANGE')
  parser_aggr.add_argument('-c', '--critical', metavar='RANGE', default='',
                    help='return critical if load is outside RANGE')
  parser_aggr.add_argument('-d', '--diskcount', default='',
                    help='number of expected disks')
  # check fcp
  parser_aggr = subparsers.add_parser('fcp')
  # check snapmirror
  parser_aggr = subparsers.add_parser('snapmirror')
  parser_aggr.add_argument('--lag', default='',
                    help='regex matching the name of the aggregate')
  parser_aggr.add_argument('--volume', default='',
                    help='regex matching the name of the aggregate')
  parser_aggr.add_argument('--vserver', default='',
                    help='regex matching the name of the aggregate')
  parser_aggr.add_argument('--exclude', default='',
                    help='regex matching the name of the aggregate')
  parser_aggr.add_argument('--regexp', default='',
                    help='regex matching the name of the aggregate')
  # check sparedisks
  parser_aggr = subparsers.add_parser('sparedisks')
  # check volume
  parser_aggr = subparsers.add_parser('volume')
  parser_aggr.add_argument('-w', '--size-warning', dest='size_warning', metavar='RANGE', default='',
                    help='return warning if space is outside RANGE')
  parser_aggr.add_argument('-c', '--size-critical', dest='size_critical', metavar='RANGE', default='',
                    help='return critical if space is outside RANGE')
  parser_aggr.add_argument('--inode-warning', dest='inode_warning', metavar='RANGE', default='',
                    help='return warning if inode is outside RANGE')
  parser_aggr.add_argument('--inode-critical', dest='inode_critical', metavar='RANGE', default='',
                    help='return critical if inode is outside RANGE')
  parser_aggr.add_argument('--snap-warning', dest='snap_warning', metavar='RANGE', default='',
                    help='return warning if snap is outside RANGE')
  parser_aggr.add_argument('--snap-critical', dest='snap_critical', metavar='RANGE', default='',
                    help='return critical if snap is outside RANGE')
  parser_aggr.add_argument('--snap-ignore', dest='snap_ignore', metavar='store_true', default=False,
                    help='ignore snap')
  parser_aggr.add_argument('-V', '--volume', default='',
                    help='select volume')
  parser_aggr.add_argument('--volumelist', default='',
                    help='select volume list')
  parser_aggr.add_argument('--vserver', default='',
                    help='select vserver/svm')
  parser_aggr.add_argument('--regexp', default='',
                    help='regex matching the name of the volume')
  parser_aggr.add_argument('--exclude', default='',
                    help='exclude volume list')
  # check metrocluster_state
  parser_aggr = subparsers.add_parser('metrocluster_state')
  # check quota
  parser_aggr = subparsers.add_parser('quota')
  parser_aggr.add_argument('-w', '--warning', metavar='RANGE', default='0',
                    help='return warning if load is outside RANGE')
  parser_aggr.add_argument('-c', '--critical', metavar='RANGE', default='',
                    help='return critical if load is outside RANGE')
  parser_aggr.add_argument('-V', '--volume', default='',
                    help='regex matching the name of the aggregate')
  parser_aggr.add_argument('-t', '--target', default='',
                    help='regex matching the name of the aggregate')
  parser_aggr.add_argument('--vserver', default='',
                    help='regex matching the name of the aggregate')
  # check volume_health
  parser_aggr = subparsers.add_parser('volume_health')
  # check node_cpu
  parser_aggr = subparsers.add_parser('node_cpu')
  parser_aggr.add_argument('-w', '--warning', metavar='RANGE', default='',
                    help='return warning if load is outside RANGE')
  parser_aggr.add_argument('-c', '--critical', metavar='RANGE', default='',
                    help='return critical if load is outside RANGE')
  # check node_health
  parser_aggr = subparsers.add_parser('node_health')
  args = parser.parse_args()

  if args.check == 'aggr':
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
        DiskScalarContext(args.check, args.warning, args.critical, diskcount=args.diskcount)) #, AggrSummary())
  elif args.check == 'fcp':
    check = nagiosplugin.Check(
        Fcp(args.hostname, args.username, args.password, args.insecure),
        AdvancedScalarContext(args.check)) #, AggrSummary())
  elif args.check == 'snapmirror':
    check = nagiosplugin.Check(
        Snapmirror(args.hostname, args.username, args.password, args.insecure, args.volume, args.vserver, args.exclude, args.regexp),
        SnapmirrorScalarContext(args.check, lag=args.lag)) #, AggrSummary())
  elif args.check == 'sparedisks':
    check = nagiosplugin.Check(
        Sparedisks(args.hostname, args.username, args.password, args.insecure),
        nagiosplugin.ScalarContext(args.check, critical='0:0')) #, AggrSummary())
  elif args.check == 'volume':
    check = nagiosplugin.Check(
        Volume(args.hostname, args.username, args.password, args.insecure, args.snap_ignore, args.volume, args.volumelist, args.vserver, args.regexp, args.exclude),
        VolumeScalarContext(args.check, args.size_warning, args.size_critical, inode_warning=args.inode_warning, inode_critical=args.inode_critical, snap_warning=args.snap_warning, snap_critical=args.snap_critical)) #, AggrSummary())
  elif args.check == 'metrocluster_state':
    check = nagiosplugin.Check(
        Metrocluster_State(args.hostname, args.username, args.password, args.insecure),
        AdvancedScalarContext(args.check)) #, AggrSummary())
  elif args.check == 'quota':
    check = nagiosplugin.Check(
        Quota(args.hostname, args.username, args.password, args.insecure, args.volume, args.target, args.vserver),
        nagiosplugin.ScalarContext(args.check, args.warning, args.critical)) #, AggrSummary())
  elif args.check == 'volume_health':
    check = nagiosplugin.Check(
        Volume_Health(args.hostname, args.username, args.password, args.insecure),
        AdvancedScalarContext(args.check)) #, AggrSummary())
  elif args.check == 'node_cpu':
    check = nagiosplugin.Check(
        Node_Cpu(args.hostname, args.username, args.password, args.insecure),
        nagiosplugin.ScalarContext(args.check, args.warning, args.critical)) #, AggrSummary())
  elif args.check == 'node_health':
    check = nagiosplugin.Check(
        Node_Health(args.hostname, args.username, args.password, args.insecure),
        AdvancedScalarContext(args.check)) #, AggrSummary())
  else:
    sys.exit('Check does not exist. Use --help')
  check.main(verbose=args.verbose)

if __name__ == '__main__':
  main()