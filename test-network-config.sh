#!/bin/sh
# LXC container config - connects to br-cx bridge
# These containers simulate network hosts connected to the Aruba CX switch

# host1 - access port on VLAN 100
lxc.net.0.type = veth
lxc.net.0.link = br-cx
lxc.net.0.flags = up
lxc.net.0.hwaddr = 00:16:3e:aa:bb:01

# host2 - access port on VLAN 200
lxc.net.0.type = veth
lxc.net.0.link = br-cx
lxc.net.0.flags = up
lxc.net.0.hwaddr = 00:16:3e:aa:bb:02

# server1 - trunk port with multiple VLANs
lxc.net.0.type = veth
lxc.net.0.link = br-cx
lxc.net.0.flags = up
lxc.net.0.hwaddr = 00:16:3e:aa:bb:03

# router1 - inter-VLAN routing
lxc.net.0.type = veth
lxc.net.0.link = br-cx
lxc.net.0.flags = up
lxc.net.0.hwaddr = 00:16:3e:aa:bb:04

# host3 - access port on VLAN 100
lxc.net.0.type = veth
lxc.net.0.link = br-cx
lxc.net.0.flags = up
lxc.net.0.hwaddr = 00:16:3e:aa:bb:05