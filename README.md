# ontap_check

This check script for Nagios/Icinga offers a variety of checks for NetApp ONTAP systems. The check script uses the NetApp Rest API to get the necessary information for the checks. The Rest API is supported since ONTAP version 9.6. The checks are inspired by checks written in Perl that are using the deprecated ZAPI.

## available checks

check | short description
:--- | ---:
[aggr](#aggregate-check) | check aggregate real space usage
[clusterlinks](#clusterlinks-check) | check HA-interconnect and cluster links
[global](#global-check) | check power supplies, fans, nvram status, temp or global health
[disk](#disk-check) | check netapp system disk state
[multipath](#multipath-check) | check if all disks are multipathed (4 paths)
[fcp](#fibre-channel-port-check) | check fcp interfaces
[interface_health](#interface-health-check) | check interface status, home-node and home-port
[port_health](#port-health-check) | check if port is enabled and up
[snapmirror](#snapmirror-check) | check snapmirror healthness
[sparedisks](#sparedisks-check) | check netapp system spare disks
[volume](#volume-check) | check volume usage
[metrocluster_state](#metrocluster-state-check) | check metrocluster state
[metrocluster_config](#metrocluster-config-check) | check metrocluster config replication
[metrocluster_check](#metrocluster-check) | netapp mcc metrocluster check
[metrocluster_aggr](#metrocluster-aggregate-check) | check metrocluster aggregate state
[quota](#quota-check) | check quota usage
[volume_health](#volume-health-check) | check volume health
[node_health](#node-health-check) | check node health
[node_cpu](#node-cpu-utilization-check) | node cpu utilization

## command line options

### global options

usage: `ontap_check.py [-h] [-H HOSTNAME] [-U USERNAME] [-p PASSWORD] [-i] [-v]
                      {aggr,clusterlinks,global,disk,multipath,fcp,interface_health,port_health,snapmirror,sparedisks,volume,metrocluster_state,metrocluster_config,metrocluster_check,metrocluster_aggr,quota,volume_health,node_health,node_cpu}
                      ...`

ontap_check - Check script for Nagios/Icinga

positional arguments:
```
  {aggr,clusterlinks,global,disk,multipath,fcp,interface_health,port_health,snapmirror,sparedisks,volume,metrocluster_state,metrocluster_config,metrocluster_check,metrocluster_aggr,quota,volume_health,node_health,node_cpu}
```

optional arguments:
```
  -h, --help            show this help message and exit
  -H HOSTNAME, --hostname HOSTNAME
  -U USERNAME, --username USERNAME
  -p PASSWORD, --password PASSWORD
  -i, --insecure        disable ssl certificate check
  -v, --verbose         increase output verbosity (use up to 3 times)
  --ignore-missing      Ignore entities which do not exist
  --no-perf-data        Do not show performance data
```

### check options

#### aggregate check

usage: `ontap_check.py aggr [-h] [-w RANGE] [-c RANGE] [-r REGEXP] [-A AGGR]`

aggr - check aggregate real space usage

optional arguments:
```
  -h, --help            show this help message and exit
  -w RANGE, --warning RANGE
                        return warning if load is outside RANGE
  -c RANGE, --critical RANGE
                        return critical if load is outside RANGE
  -r REGEXP, --regexp REGEXP
                        regex matching the name of the aggregate
  -A AGGR, --aggr AGGR  aggregate name
```

#### clusterlinks check

usage: `ontap_check.py clusterlinks [-h]`

clusterlinks - check HA-interconnect and cluster links

optional arguments:
```
  -h, --help  show this help message and exit
```

#### global check

usage: `ontap_check.py global [-h] [--plugin PLUGIN]`

global - check powersupplies, fans, nvram status, temp or global health

optional arguments:
```
  -h, --help       show this help message and exit
  --plugin PLUGIN  plugin choices are power, fan, nvram, temp, health
```

#### disk check

usage: `ontap_check.py disk [-h] [-w RANGE] [-c RANGE] [-d DISKS]`

disk - check netapp system disk state

optional arguments:
```
  -h, --help            show this help message and exit
  -w RANGE, --warning RANGE
                        return warning if load is outside RANGE
  -c RANGE, --critical RANGE
                        return critical if load is outside RANGE
  -d DISKS, --disks DISKS
                        number of expected disks
```

#### multipath check

usage: `ontap_check.py multipath [-h]`

multipath - check if all disks are multipathed (4 paths)

optional arguments:
```
  -h, --help  show this help message and exit
```

#### fibre channel port check

usage: `ontap_check.py fcp [-h]`

fcp - check fcp interfaces

optional arguments:
```
  -h, --help  show this help message and exit
```

#### interface health check

usage: `ontap_check.py interface_health [-h]`

interface_health - check interface status, home-node and home-port

optional arguments:
```
  -h, --help  show this help message and exit
```

#### port health check

usage: `ontap_check.py port_health [-h]`

port_health - check if port is enabled and up

optional arguments:
```
  -h, --help  show this help message and exit
```

#### snapmirror check

usage: `ontap_check.py snapmirror [-h] [--lag LAG] [--volume VOLUME]
                                 [--vserver VSERVER] [--exclude EXCLUDE]
                                 [--regexp]`

snapmirror - check snapmirror healthness

optional arguments:
```
  -h, --help         show this help message and exit
  --lag LAG          delay in seconds, default 28h
  --volume VOLUME    name of the destination volume
  --vserver VSERVER  name of the destination svm
  --exclude EXCLUDE  list of volume names to be excluded
  --regexp           enable regexp matching for the exclusion list
```

#### sparedisks check

usage: `ontap_check.py sparedisks [-h]`

sparedisks - check netapp system spare disks

optional arguments:
```
  -h, --help  show this help message and exit
```

#### volume check

usage: `ontap_check.py volume [-h] [-w RANGE] [-c RANGE]
                             [--inode-warning RANGE] [--inode-critical RANGE]
                             [--snap-warning RANGE] [--snap-critical RANGE]
                             [--snap-ignore store_true] [-V VOLUME]
                             [--volumelist VOLUMELIST] [--vserver VSERVER]
                             [--regexp REGEXP] [--exclude EXCLUDE]`

volume - check volume usage

optional arguments:
```
  -h, --help            show this help message and exit
  -w RANGE, --size-warning RANGE
                        return warning if space is outside RANGE
  -c RANGE, --size-critical RANGE
                        return critical if space is outside RANGE
  --inode-warning RANGE
                        return warning if inode is outside RANGE
  --inode-critical RANGE
                        return critical if inode is outside RANGE
  --snap-warning RANGE  return warning if snap is outside RANGE
  --snap-critical RANGE
                        return critical if snap is outside RANGE
  --snap-ignore store_true
                        ignore snap
  -V VOLUME, --volume VOLUME
                        select volume
  --volumelist VOLUMELIST
                        select volume list
  --vserver VSERVER     select vserver/svm
  --regexp REGEXP       regex matching the name of the volume
  --exclude EXCLUDE     exclude volume list
```

#### metrocluster state check

usage: `ontap_check.py metrocluster_state [-h]`

metrocluster_state - check metrocluster state

optional arguments:
```
  -h, --help  show this help message and exit
```

#### metrocluster config check

usage: `ontap_check.py metrocluster_config [-h]`

metrocluster_config - check metrocluster config replication

optional arguments:
```
  -h, --help  show this help message and exit
```

#### metrocluster check

usage: `ontap_check.py metrocluster_check [-h]`

metrocluster_check - netapp mcc metrocluster check

optional arguments:
```
  -h, --help  show this help message and exit
```

#### metrocluster aggregate check

usage: `ontap_check.py metrocluster_aggr [-h]`

metrocluster_aggr - check metrocluster aggregate state

optional arguments:
```
  -h, --help  show this help message and exit
```

#### quota check

usage: `ontap_check.py quota [-h] [-w RANGE] [-c RANGE] [-V VOLUME] [-t TARGET]
                            [--vserver VSERVER]`

quota - check quota usage

optional arguments:
```
  -h, --help            show this help message and exit
  -w RANGE, --warning RANGE
                        warning threshold for used space in percent
  -c RANGE, --critical RANGE
                        critical threshold for used space
  -V VOLUME, --volume VOLUME
                        name of the volume to check the quotas on
  -t TARGET, --target TARGET
                        name of the qtree to check the quota
  --vserver VSERVER     name of the svm to check quotas on
```

#### volume health check

usage: `ontap_check.py volume_health [-h]`

volume_health - check volume health

optional arguments:
```
  -h, --help  show this help message and exit
```

#### node health check

usage: `ontap_check.py node_health [-h]`

node_health - check node health

optional arguments:
```
  -h, --help  show this help message and exit
```

#### node cpu utilization check

usage: `ontap_check.py node_cpu [-h] [-w RANGE] [-c RANGE]`

node_cpu - node cpu utilization

optional arguments:
```
  -h, --help            show this help message and exit
  -w RANGE, --warning RANGE
                        return warning if cpu usage in percent is outside RANGE
  -c RANGE, --critical RANGE
                        return critical if cpu usage in percent is outside RANGE
```
