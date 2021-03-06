from pox.core import core
from pox.lib.revent import revent
import pox.openflow.libopenflow_01 as of
from pox.lib.addresses import IPAddr
from pox.lib.addresses import EthAddr
import pox.openflow.spanning_tree
import asyncore
import mysql.connector
import struct
import asynchat
import socket
import thread
import os
import RouteApp
import threading
import time
import pyinotify
import random

log = core.getLogger()
SNORT_ADDR = "10.0.0.11"
ip2serv_name = {"10.0.0.1" : "http", "10.0.0.2" : "http"}
serv_name2ip = {"http" : ["10.0.0.1", "10.0.0.2"]}
gateway_mac=EthAddr("fe:05:8f:33:8c:27")
MAXCMD = 100
HIGHER = 5
HIGH = 4
MID = 3
LOWMID = 2
LOW = 1
SNORT_DPID = 12002709798223L

def start_server(socket_map):
    asyncore.loop(map = socket_map)

def start_watch(wm, eh):
    notifier = pyinotify.Notifier(wm, eh)
    notifier.loop()

class MyEventHandler(pyinotify.ProcessEvent):
    log.info("Starting monitor...")

    def gen_cmd(self, pathname):
        try:
            fd = open(pathname, 'r')
            commands = fd.readlines(MAXCMD)
            fd.close()
            return commands
        except IOError as e:
            log.error("I/O error ({0}): {1}".format(e.errno, e.strerror))
        return -1
    def func_gen(self, event):
        commands = self.gen_cmd(event.pathname)
        if not commands == -1:
            core.secure.func_gen(event.name, commands)
            func_name = event.name
            value = func_name.split('_')
            if not core.secure.func_table.has_key(value[0]):
                core.secure.func_table[value[0]]={}
            if not core.secure.func_table[value[0]].has_key(value[1]):
                core.secure.func_table[value[0]][value[1]] = {}
            if (len(value) == 4):
                core.secure.func_table[value[0]][value[1]][(value[2],value[3])] = func_name
            else:
                core.secure.func_table[value[0]][value[1]]["any"] = func_name
        
    def func_del(self, event):
        func_name = "func_" + event.name
        try:
            funcname = func_name.replace(" ", "_")
            core.secure.funclist.remove(func_name)
            delattr(core.secure.handlers, funcname)
            value = func_name.split('_')
            del value[0]
            if (len(value) == 4):
                del core.secure.func_table[value[0]][value[1]][(value[2],value[3])]
            else:
                del core.secure.func_table[value[0]][value[1]]["any"]
            log.info("handler %s removed, rules updated."%funcname)
        except ValueError as e:
            log.error('%s is not in the funclist'%func_name)

    def process_IN_MOVED_TO(self, event):
        log.debug('MOVED_TO event: %s'%event.name)
        self.func_gen(event)
        
    def process_IN_MODIFY(self, event):
        log.debug('MODIFY event: %s'%event.name)
        self.func_del(event)
        self.func_gen(event)

    def process_IN_DELETE(self, event):
        log.debug('DELETE event: %s'%event.name)
        self.func_del(event)

    def process_IN_MOVED_FROM(self, event):
        log.debug('MOVED_FROM event: %s', event.name)
        self.func_del(event)

class AlertIn(revent.Event):

    def __init__(self, alertmsg):
        revent.Event.__init__(self)
        self.name = alertmsg[0]
        self.priority = alertmsg[1]
        self.src = alertmsg[2]
        self.dst = alertmsg[3]
        self.occation  = alertmsg[4]

class Reminder(revent.EventMixin):

    _eventMixin_events = set([
        AlertIn,
        ])
    def __init__(self):
        self.msg = None

    def set_msg(self, msg):
        self.msg = msg

    def alert(self):
        self.raiseEvent(AlertIn, self.msg)

class secure_connect(asynchat.async_chat):

    def __init__(self, connection, socket_map):
        asynchat.async_chat.__init__(self, connection, map = socket_map)
        self.buf = []
        self.ac_in_buffer_size = 1024
        self.set_terminator("@")

    def collect_incoming_data(self, data):
        self.buf.append(data)

    def found_terminator(self):
        temp = ("".join(self.buf)).split("\n")
        core.Reminder.set_msg(temp)
        core.Reminder.alert()
        self.buf=[]
        self.set_terminator("@")

class secure_server(asyncore.dispatcher):
    def __init__(self, socket_map):
        self.socket_map = socket_map
        asyncore.dispatcher.__init__(self, map = self.socket_map)
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.bind(("0.0.0.0",20000))
        self.listen(5)
        
    def handle_accept(self):
        connection, addr = self.accept()
        server_connect = secure_connect(connection, self.socket_map)

class handlers(object):
    def __init__(self):
        pass

class secure(object):
    def start(self):
        core.openflow.addListeners(self)
        core.openflow_discovery.addListeners(self)
    
    def __init__(self, path):
        self.path = path
        self.filelist=None
        self.counter=0
        self.filenum=0
        self.cmdlist = ["disconnect", "wait", "reconnect", "pass", "monitor", "reset", "redirect", "unredirect", "passit", "refuse"]
	self.handlers = handlers()
        self.funclist = None
        self.func_table={}
        self.alys_cmd()
        self.action_triggered = False 
        
        self.name_process()

        self.mactable = {}
        self.iptable = {}
        self.droplist = {}
        self.monitorlist = {}
        self.redirectlist = {}
        
        self.ignorelist = []
        
        self.dangerlist = {}
        self.blacklist = {}
        
        self.socket_map = {}
        self.server = secure_server(self.socket_map)
        core.Reminder.addListeners(self)
        core.addListener(pox.core.GoingUpEvent, self.start_server)
        core.call_when_ready(self.start, ["openflow_discovery"])
        core.callDelayed(1, self.start_watch)

    def start_server(self, event):
        thread.start_new_thread(start_server, (self.socket_map,))

    def start_watch(self):
        wm = pyinotify.WatchManager()
        wm.add_watch(self.path, pyinotify.ALL_EVENTS, rec = True)
        eh = MyEventHandler()
        thread.start_new_thread(start_watch, (wm, eh))

    def func_gen(self, File, cmds):
        func_name = "func_" + File
        self.funclist.append(func_name)
        func_name = func_name.replace(" ", "_")
        cmdgenlist = []
        for each in cmds:
            item = each.split('\n')
            action=item[0].split(',')
            if action[0]=="time":
                action[1]=float(action[1])
                func_action = "self."+action[0]+"("+action[1]+")"
            elif action[0] in self.cmdlist:
                if(len(action) == 1):
                    func_action = "self." + action[0] + "()"
                else:
                    func_action = "self."+action[0]+"("+action[1]+")"
            cmdgenlist.append(func_action)
            func_action = ''

        function = "def "+func_name+"(self, src, dst):\n"
        for command in cmdgenlist:
            function = function+"    "+command+"\n"
        exec function        
        setattr(self.handlers, func_name, eval(func_name))
        log.info("handler %s registered, rules updated."%func_name)


    def alys_file(self):
        for File in self.filelist:
            fd = open(self.path + File,'r')
            commands = fd.readlines(MAXCMD)
            fd.close()
            yield File, commands

    def alys_cmd(self):
        self.filelist = os.listdir(self.path)
        self.funclist = []
        self.filenum = len(self.filelist)
        filegen = self.alys_file()
        while self.counter < self.filenum:
            File,commands = filegen.next()
            self.func_gen(File, commands)
            self.counter += 1

    def passit(self):
        self.action_triggered = True

    def refuse(self,addr):
        self.action_triggered = False
        if len(self.blacklist) > 0:
            for ip in self.blacklist.keys():
                if self.blacklist[ip] == 0:
                    msg = of.ofp_flow_mod()
                    msg.priority = HIGHER
                    msg.match.dl_type = 0x0800
                    msg.match.nw_src = IPAddr(ip)
                    msg.actions.append(of.ofp_action_output(port = of.OFPP_NONE))
	            core.openflow.getConnection(SNORT_DPID).send(msg)
                    self.blacklist[ip] = 1;
                    log.info("all the request of %s being refused"%ip)
        self.action_triggered = True
        
    def disconnect(self,addr):
        self.action_triggered = False
        if self.droplist.has_key(addr):
            self.droplist[addr] += 1
        else:
            self.droplist[addr] = 1
        if self.droplist[addr] != 1:
            return
        ipaddr = IPAddr(addr)
        msg = of.ofp_flow_mod()
        msg.priority = MID
        if self.iptable.has_key(ipaddr) and self.iptable[ipaddr] != gateway_mac:
            #Forbid inside machine from sending packets
            host_mac = self.iptable[ipaddr]
            switchid = self.mactable[host_mac][0]
            msg.match.dl_type = 0x0800
            msg.match.dl_src = host_mac
            msg.actions.append(of.ofp_action_output(port = of.OFPP_NONE))
        else:
            switchid = self.mactable[gateway_mac][0]
            msg.match.dl_type = 0x0800
            msg.match.nw_src = ipaddr
            msg.actions.append(of.ofp_action_output(port = of.OFPP_NONE))
        switch = core.openflow.getConnection(switchid)
        switch.send(msg)
        self.action_triggered = True
        log.info("%s being disconncted"%addr)
    

    def redirect(self,addr):
        self.action_triggered = False
        ipaddr = IPAddr(addr)
        if not ip2serv_name.has_key(addr):
            return
        if self.redirectlist.has_key(addr):
            self.redirectlist[addr] += 1
        else:
            self.redirectlist[addr] = 1
        if self.redirectlist[addr] == 1:
            if self.droplist.has_key(addr):
                if ip2serv_name.has_key(addr):
                    serv_name = ip2serv_name[addr]
                    if serv_name2ip.has_key(serv_name):
                    	Masterip = serv_name2ip[serv_name][0]
                    	Masteraddr = IPAddr(Masterip)
                        livelist = [ item for item in serv_name2ip[serv_name] if item not in self.droplist ]
                        if len(livelist) > 0:
                            new_ip = random.choice(livelist)
                            log.info("redirecting for %s to %s \nin the service of %s"%(addr, str(new_ip), serv_name))
                            new_mac = self.iptable[IPAddr(new_ip)]
                            msg = of.ofp_flow_mod()
                            msg.match.dl_dst = self.iptable[Masteraddr]
                            msg.actions.append(of.ofp_action_dl_addr.set_dst(new_mac))
                            msg.actions.append(of.ofp_action_nw_addr.set_dst(IPAddr(new_ip)))
                            msg.priority = HIGH
                            routelist = RouteApp.get_shortest_route(pox.openflow.spanning_tree._calc_spanning_tree(), self.mactable[gateway_mac][0], self.mactable[new_mac][0])
                            routelist[-1] = self.mactable[new_mac]
                            msg.actions.append(of.ofp_action_output(port = routelist[0][1]))
                            switchid = self.mactable[gateway_mac][0]
                            switch = core.openflow.getConnection(switchid)
                            switch.send(msg)
                            msg = of.ofp_flow_mod()
                            msg.match.dl_src = self.iptable[IPAddr(new_ip)]
                            msg.match.dl_dst = gateway_mac
                            msg.priority = HIGH
                            msg.actions.append(of.ofp_action_dl_addr.set_src(self.iptable[ipaddr]))
                            msg.actions.append(of.ofp_action_nw_addr.set_src(ipaddr))
                            msg.actions.append(of.ofp_action_output(port = self.mactable[gateway_mac][1]))
                            switchid = self.mactable[gateway_mac][0]
                            switch = core.openflow.getConnection(switchid)
                            switch.send(msg)
                            self.action_triggered = True
                        else:
                            log.error("no more same service ip to redirect")
                    else:
                        log.error("check the service to ip dictionary %s"%serv_name)
                else:
                    log.error("check the ip to service dictionary %s"%addr)
            else:
                log.error("%s is not in droplist"%addr)
    
    def wait(self,arg):
        #if self.action_triggered:
        log.info("waiting for %d seconds"%arg)
        time.sleep(arg)

    def reconnect(self,addr):
        self.action_triggered = False
        self.droplist[addr] -= 1
        if self.droplist[addr] <= 0:
            ipaddr = IPAddr(addr)
            self.droplist[addr] = 0
            log.info("%s being reconnected"%addr)
            msg = of.ofp_flow_mod()
            msg.command = of.OFPFC_DELETE_STRICT
            msg.priority = MID
            msg.actions.append(of.ofp_action_output(port = of.OFPP_NONE))
            if self.iptable.has_key(ipaddr) and self.iptable[ipaddr] != gateway_mac:
                host_mac = self.iptable[ipaddr]
                switchid = self.mactable[host_mac][0]
                msg.match.dl_type = 0x0800
                msg.match.dl_src = host_mac
            else:
                switchid = self.mactable[gateway_mac][0]
                msg.match.dl_type = 0x0800
                msg.match.nw_src = ipaddr
            switch = core.openflow.getConnection(switchid)
            switch.send(msg)
            self.action_triggered = True
    
    def monitor(self, addr):
        self.action_triggered = False
        ipaddr = IPAddr(addr)
        if not self.iptable.has_key(ipaddr):
            return
        if self.iptable[ipaddr] == gateway_mac:
            return
        if self.monitorlist.has_key(addr):
            self.monitorlist[addr] += 1
        else:
            self.monitorlist[addr] = 1
        if self.monitorlist[addr] == 1:
            log.info("packet from/to %s mirrored for monitoring"%addr)
            msg = of.ofp_flow_mod()
            msg.priority = LOWMID
            msg.match.dl_src = self.iptable[ipaddr]
            msg.match.dl_type = 0x0800
            msg.actions.append(of.ofp_action_dl_addr.set_dst(gateway_mac))
            routelist = RouteApp.get_shortest_route(pox.openflow.spanning_tree._calc_spanning_tree(), self.mactable[self.iptable[ipaddr]][0], self.mactable[gateway_mac][0])
            routelist[-1] = self.mactable[gateway_mac]
            msg.actions.append(of.ofp_action_output(port = routelist[0][1]))
            switchid = self.mactable[self.iptable[ipaddr]][0]
            switch = core.openflow.getConnection(switchid)
            switch.send(msg)
            self.action_triggered = True

    #delete all flow entries in flowtable 1
    def reset(self, addr):
        self.action_triggered = False
        self.monitorlist[addr] -= 1
        if self.monitorlist[addr] > 0:
            return
        self.monitorlist[addr] = 0
        log.info("resetting %s"%addr)
        msg = of.ofp_flow_mod()
        msg.command = of.OFPFC_DELETE_STRICT
        ipaddr = IPAddr(addr)
        host_mac = self.iptable[ipaddr]
        msg.match.dl_src = host_mac
        switchid = self.mactable[host_mac][0]
        switch = core.openflow.getConnection(switchid)
        switch.send(msg)
        self.action_triggered = True

    def unredirect(self, addr):
        self.action_triggered = False
        self.redirectlist[addr] -= 1
        if self.redirectlist[addr] > 0:
            return
        self.redirectlist[addr] = 0
        log.info("unredirecting %s"%addr)
        msg = of.ofp_flow_mod()
        msg.command = of.OFPFC_DELETE_STRICT
        msg.priority = HIGHER
        serv_name = ip2serv_name[addr]
        Masterip = serv_name2ip[serv_name][0]         
        Masteraddr = IPAddr(Masterip)
        host_mac = self.iptable[Masteraddr]
        msg.match.dl_dst = host_mac
        msg.match.of_ip_src = Masterip
        switchid = self.mactable[gateway_mac][0]
        switch = core.openflow.getConnection(switchid)
        switch.send(msg)
        self.action_triggered = True


    def name_process(self):
        for func_name in self.funclist:
            value = func_name.split('_')
            del value[0]
            if not self.func_table.has_key(value[0]):
                self.func_table[value[0]]={}
            if not self.func_table[value[0]].has_key(value[1]):
                self.func_table[value[0]][value[1]] = {}
            if (len(value) == 4):
                self.func_table[value[0]][value[1]][(value[2],value[3])] = func_name
            else:
                self.func_table[value[0]][value[1]]["any"] = func_name
        
#{priority:{signatrue:{(interval, times):funcname}}}

    def occa_process(self, occation, during):
        timeArray = time.strptime(occation, "%Y-%m-%d %H:%M:%S")
        timeStamp = time.mktime(timeArray)
        timeStamp -= float(during)
        timeArray = time.localtime(timeStamp)
        before = time.strftime("%Y-%m-%d %H:%M:%S", timeArray)
        return before
      
    def _handle_AlertIn(self, event):
        log.info("Alert In.")
        sig = event.name
        occation = event.occation
        priority = event.priority
        sip  = event.src
        dip  = event.dst

        if ip2serv_name.has_key(dip) and not self.dangerlist.has_key(dip):
            self.dangerlist[dip] = sip;
        if self.dangerlist.has_key(sip) and not self.blacklist.has_key(self.dangerlist[sip]):
            self.blacklist[self.dangerlist[sip]] = 0;

        if self.monitorlist.has_key(sip) and self.monitorlist[sip] > 0 and not sig in self.ignorelist:
            log.info("%s is under attack and may have been captured, so disconncet it."%sip)
            self.disconnect(sip)
        
        func_name = "func_"
        if self.func_table.has_key(priority):
            func_name += priority

            if self.func_table[priority].has_key(sig):
                func_name += "_" + sig
                
                if (len(self.func_table[priority][sig]) == 1) and (self.func_table[priority][sig].keys()[0] == "any"):
                    func_name += "_any"
                else:
                    timelist = [item for item in self.func_table[priority][sig].keys()]
                    flag = False
                    for time in timelist:
                        before = self.occa_process(occation, time[0])
                        times = self.sql(before, occation, sip, dip)
                        log.info("this has happened:%d times"%times)
                        if times >= int(time[1]):
                            func_name += "_" + time[0] + "_" + time[1]
                            flag = True
                            break
                    if not flag:
                        if (self.func_table[priority][sig].has_key("any")):
                            func_name += "_any"
                        else:
                            log.error("No Strategy")
                            return

            elif (self.func_table[priority].has_key("any")):
                func_name += "_any"
                
                if (len(self.func_table[priority]["any"]) == 1) and (self.func_table[priority][sig][self.func_table[priority]["any"].keys()[0]] == "any"):
                    func_name += "_any"
                else:
                    timelist = [item for item in self.func_table[priority]["any"].keys()]
                    flag = False
                    for time in timelist:
                        before = self.occa_process(occation, time[0])
                        times = self.sql(before, occation, sip, dip)
                        log.info("this has happened:%d times"%times)
                        if times >= int(time[1]):
                            func_name += "_" + time[0] + "_" + time[1]
                            flag = True
                            break
                    if not flag:
                        if (self.func_table[priority]["any"].has_key("any")):
                            func_name += "_any"
                        else:
                            log.error("No Strategy")
                            return
            else:
                log.error("No Strategy for signatrue %s"%sig)
                return

        else:
            log.error("No Strategy for priority %s"%priority)
            return
        
        func_name = func_name.replace(" ", "_")
        new_th = threading.Thread(target = getattr(self.handlers, func_name), args=(self, sip, dip))
        new_th.start()

    def sql(self, before, occation, src, dst):
        try:
            conn = mysql.connector.connect(host=SNORT_ADDR, user='root',passwd='root',db='snort')
        except Exception, e:
           log.error(e)
           sys.exit(-1)
        cursor = conn.cursor()
        cursor.execute("select count(*) as times from iphdr,event where (event.timestamp between '%s' and '%s') and (iphdr.ip_src=%d and iphdr.ip_dst=%d) and iphdr.cid=event.cid;"%(before, occation, socket.ntohl(struct.unpack("I", socket.inet_aton(src))[0]), socket.ntohl(struct.unpack("I", socket.inet_aton(dst))[0])))
        rows = cursor.fetchone()
        cursor.close()
        conn.close()
        return rows[0]
	
    def _handle_ConnectionUp(self, event):
        pass

    def _handle_PacketIn(self, event):
    
        packet = event.parsed
        #the flood method
        def flood(switch):      
	    msg = of.ofp_packet_out()
      
            msg.actions.append(of.ofp_action_output(port = of.OFPP_FLOOD))
      
            msg.data = event.ofp
            msg.in_port = event.port
            switch.send(msg)
    
    #the drop method
        def drop(switch):
	    msg = of.ofp_packet_out()
            msg.buffer_id = event.ofp.buffer_id
            msg.in_port = event.port
            switch.send(msg)
        
        ip = packet.find("ipv4")
        if ip == None:
            ip = packet.find("icmp")

        if ip:
            if not self.iptable.has_key(ip.srcip):
                self.iptable[ip.srcip] = packet.src
        if not self.mactable.has_key(packet.src):
            self.mactable[packet.src] = (event.dpid, event.port)

        if packet.type == packet.LLDP_TYPE or packet.dst.isBridgeFiltered():
            drop(event.connection)
            return
        if packet.dst.is_multicast:
            flood(event.connection)
    
        else:
            if not self.mactable.has_key(packet.dst):
	        flood(event.connection)
            else:
	        routelist = RouteApp.get_shortest_route(pox.openflow.spanning_tree._calc_spanning_tree(), event.dpid, self.mactable[packet.dst][0])
	        routelist[-1] = self.mactable[packet.dst]
	        msg = of.ofp_packet_out()
                msg.data = event.ofp
                msg.actions.append(of.ofp_action_output(port = routelist[0][1]))
                event.connection.send(msg) 
	        for switchid,out_port in routelist:
	            msg = of.ofp_flow_mod()
                    msg.table_id = 0
                    msg.priority = LOW
	            msg.match.dl_dst = packet.dst
	            msg.actions.append(of.ofp_action_output(port = out_port))
                    if switchid == SNORT_DPID:
                        msg.actions.append(of.ofp_action_output(port = 3))
                    msg.idle_timeout = 10
                    msg.hard_timeout = 30
	            switch = core.openflow.getConnection(switchid)
	            switch.send(msg)


def launch():
    path = "/home/dracula/pox/rules/"
    core.registerNew(Reminder)
    core.registerNew(secure, path)
    log.info("Secure module launched.")
