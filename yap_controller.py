#
################################################################################
# The MIT License (MIT)
#
# Copyright (c) 2022 Curt Timmerman
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
################################################################################
#
# title           :yap-controller.py
# description     :PID like controller
# author          :Curt Timmerman
# date            :20230420
# version         :0.1
# notes           :
# micropython     :1.19
#
################################################################################
#

import sys
import gc
import network
import usocket as socket

import ujson as json
import math

MACHINE_FREQ = 240000000
UDP_PORT = 5010

#---------------------------------------------------------------------------
# GetCommand
#   Example UDP command:
#     {
#     "jsonrpc": "2.0",                  # required but not used
#     "method": "set_power_level",       # required
#     "params": {"power_level": 42.2},   # required
#     "id": <INT>                        # optional
#     }
#     No result is returned
#
#   Example WEB query string (GET):
#     'GET /?power_level=42.2 ...
#     Note: This process may exceed the poll interval
#
#---------------------------------------------------------------------------
class GetCommand :

    def __init__(self,
                 poller ,
                 udp_port = UDP_PORT) :
        #print ("GetCommand: init")       
        self.poller = poller
        
        #---- UDP interface
        self.s = socket.socket(socket.AF_INET ,
                               socket.SOCK_DGRAM)
        #self.my_ip = network.WLAN().ifconfig('192.168.199.199' ,
         #                                   '255.255.255.0' ,
         #                                    '0.0.0.0' ,
         #                                    '8.8.8.8')
        self.my_ip = network.WLAN().ifconfig()[0]
        #print (network.WLAN().ifconfig())
        self.address = socket.getaddrinfo(self.my_ip, udp_port)[0][-1]
        self.address = ("", udp_port)
        self.s.bind(self.address)
        print (self.address)
        self.s.settimeout(0)
        self.yap_controller \
            = self.poller.message_set ("yap_process_value",
                                            {"process_value": None ,
                                            "last_update_ms": poller.get_current_time_ms ()})
        self.yap_settings \
            = self.poller.message_set ("yap_settings",
                                       {"settings": {} ,
                                        "last_update_ms": poller.get_current_time_ms ()})

    def poll_it (self) :
        #print_debug ("GetCommand: poll_it")
        #---- UDP input
        try :
            mess_address = self.s.recvfrom (2000)
            print ("GetC:", mess_address)
            message = mess_address[0]
            address_port = mess_address [1]
            request_json = message.decode ()
            request_dict = json.loads (request_json)
            #print ("Cmd:", request_dict)
            self.process_request (request_dict)
        except OSError :
            #print ("GetC: no data")
            pass

    def process_request (self, request) :
        #print ("request:", request)
        if not "jsonrpc" in request :
            print ("request: 'jsonrpc' missing")
            return
        if not "method" in request :
            print ("request: 'method' missing")
            return
        if not "params" in request :
            print ("request: 'params' missing")
            return
        if request["method"] == "set_process_value" :
            result = self.set_power_level (request["params"])
        elif request["method"] == "update_settings" :
            request ["params"]["temperature_update"] = \
                "current_temperature" in request ["params"]
            self.poller.message_set ("pid_settings", request["params"])
        elif request["method"] == "shutdown" :
            self.poller.shutdown ()

    def set_power_level (self, params) :
        #print ("set_power_level:", params)
        if not "power_level" in params :
            return
        try :
            #print (new_power_level)
            new_power_level = round (float (params ['power_level']), 1)
            self.poller.message_set ("powercontrol",
                                          {"power_level": new_power_level})
        except :
            return
        #self.poller.message_set ("powercontrol",
                                      #{"power_level": new_power_level ,
                                       #"last_update_ms" : poller.get_current_time_ms ()
                                       #})

    def shutdown (self) :
        self.s.close ()

# end GetCommand

#---------------------------------------------------------------------------
# YAPController
#---------------------------------------------------------------------------
class YAPController :

    def __init__ (self ,
                  looper ,           # None if not using poll-looper
                  target_PV ,        # Temperature
                  SP ,               # Initial duty cycle (0-100%)
                  **kwargs) :
                  # **kwargs:
                  #PV_low = None ,
                  #PV_high = None ,
                  #) :
        self.looper = looper
        self.target_PV = target_PV
        self.pi_over_2_low = None
        self.pi_over_2_high = None
#        self.clip_factor = 0.9
        self.clip_factor_low = 0.9
        self.clip_factor_high = 0.9
        self.process_value = None
        self.control_range_low = None
        self.control_range_high = None
        self.new_settings (SP, **kwargs)
        self.duty_cycle = -1
        self.set_duty_cycle (SP)

    def new_settings (self ,
                      duty_cycle = None ,
                      control_range_low = None ,
                      control_range_high = None ,
                      clip_factor = None ,
                      clip_factor_low = None ,
                      clip_factor_high = None
                      ) :
        #print ("new_settings:")
        if not duty_cycle is None :
            self.duty_cycle = duty_cycle
        if control_range_low is not None :
            self.control_range_low = control_range_low
        if control_range_high is not None :
            self.control_range_high = control_range_high

        if clip_factor is not None :
            self.clip_factor_low = clip_factor
            self.clip_factor_high = clip_factor
        if clip_factor_low is not None :
            self.clip_factor_low = clip_factor_low
        if clip_factor_high is not None :
            self.clip_factor_high = clip_factor_high
        self.pi_over_2_low = (math.pi / 2.0) * self.clip_factor_low
        self.tan_factor_low = 1.0 / math.tan (self.pi_over_2_low)
        self.pi_over_2_high = (math.pi / 2.0) * self.clip_factor_high
        self.tan_factor_high = 1.0 / math.tan (self.pi_over_2_high)

    def new_PV (self, process_value) :
        #print ("new_PV:", process_value)
        if process_value == self.process_value :
            return self.duty_cycle
        self.process_value = process_value
        self.set_duty_cycle (self.get_duty_cycle (self.process_value))

    def get_duty_cycle (self ,
                        process_value) :
        if process_value <= self.control_range_low :
            return 100.0
        if process_value >= self.control_range_high :
            return 0.0                        # should probably not happen
        # Calculate new duty cycle
        if process_value < self.target_PV :
            temp_per_cent = (self.target_PV - process_value) \
                            / (self.target_PV - self.control_range_low)
            radian = self.pi_over_2_low * temp_per_cent
            duty_pc = math.tan (radian) * self.tan_factor_low
        else :
            temp_per_cent = (process_value - self.target_PV) \
                            / (self.control_range_high - self.target_PV)
            radian = self.pi_over_2_high * temp_per_cent
            duty_pc = math.tan (radian) * self.tan_factor_high
        if process_value < self.target_PV :
            new_duty = self.duty_cycle + (100 - self.duty_cycle) * duty_pc
        else :
            new_duty = self.duty_cycle - (self.duty_cycle * duty_pc)
        return new_duty

    #---- set SP
    def set_duty_cycle (self ,
                        new_duty_cycle) :
        if new_duty_cycle != self.duty_cycle :
            self.duty_cycle = new_duty_cycle   # set change indicator?
        return (self.duty_cycle)

    def plot (self ,
              start_pv = None ,
              end_pv = None ,
              process_value = None ,
              incr = 1.0 ,
              out_file = sys.stdout) :
        if not start_pv is None :
            pv = start_pv
        else :
            pv = self.control_range_low - 10
        if not end_pv is None :
            pv_last = end_pv
        else :
            pv_last = self.control_range_high + 10

        print ("# Start of process_value/duty_cycle plot data", file=out_file)
        while pv <= pv_last :
            print (pv ,                         # process_value
                   self.get_duty_cycle (pv) ,   # new duty_cycle
                   file=out_file)
            pv += incr                          # next process_value
        print ("# End of process_value/duty_cycle plot data", file=out_file)
    def gnuplot (self, *args, **kwargs) :
        #print ("gnuplot: entry")
        if "out_file" in kwargs :
            out_file = kwargs["out_file"]
        else :
            out_file = sys.stdout

        process_value = None
        start_pv = self.control_range_low - 10
        end_pv = self.control_range_high + 30      # room for labels
        duty_cycle = None
        if 'process_value' in kwargs :
            process_value = kwargs ['process_value']
        else :
            process_value = self.process_value   # May be None
        if 'start_pv' in kwargs :
            start_pv = kwargs ['start_pv']
        if 'end_pv' in kwargs :
            end_pv = kwargs ['end_pv']

        print ("$DutyCycle << EOD", file=out_file)
        self.plot (*args, **kwargs)
        print ('EOD', file=out_file)
        
        print ("$tPV << EOD", file=out_file)
        print (self.target_PV, 0, file=out_file)
        print (self.target_PV, 100, file=out_file)
        print ('EOD', file=out_file)
        if process_value is not None :
            duty_cycle = self.get_duty_cycle (process_value)
            dc_offset = 3              # for graph crosshair formatting
            #pv_offset = int ((start_pv - end_pv) * (dc_offset / 100))
            print ("$PV << EOD", file=out_file)
            print (process_value, (duty_cycle - dc_offset), file=out_file)
            print (process_value, (duty_cycle + dc_offset), file=out_file)
            print ('EOD', file=out_file)
            print ("$Inter << EOD", file=out_file)
            print (process_value, duty_cycle, file=out_file)
            print (self.target_PV, duty_cycle, file=out_file)
            print ('EOD', file=out_file)

        print ("$SP << EOD", file=out_file)
        print (self.control_range_low, self.duty_cycle, file=out_file)
        print (self.control_range_high, self.duty_cycle, file=out_file)
        print ('EOD', file=out_file)

        print ("$lCR << EOD", file=out_file)
        print (self.control_range_low, 0, file=out_file)
        print (self.control_range_low, 100, file=out_file)
        print ('EOD', file=out_file)
        
        print ("$hCR << EOD", file=out_file)
        print (self.control_range_high, 0, file=out_file)
        print (self.control_range_high, 100, file=out_file)
        print ('EOD', file=out_file)

        print ('set print "-"', file=out_file)
        print ('set border 15 linewidth 2 linecolor rgb "black"', file=out_file)
        print ('set style line 1 linewidth 3 linecolor rgb "red"', file=out_file)
        print ('set style line 2 linewidth 2 linecolor rgb "#4B0082"', file=out_file)
        print ('set style line 3 linewidth 2 linecolor rgb "green"', file=out_file)
        print ('set style line 4 linewidth 1 linecolor rgb "brown"', file=out_file)
        print ('set style line 5 linewidth 2 linecolor rgb "blue"', file=out_file)
        print ('set dashtype 5 (5,5)', file=out_file)
        print ('set title "Temperature (PV) vs Duty Cycle (SP)"', file=out_file)
        print ('set xlabel "Temperature (process value)"', file=out_file)
        #print ('set xrange [100:300] noextend', file=out_file)
        print ('set xrange [{:.0f}:{:.0f}] noextend'.format (start_pv, end_pv) ,
               file=out_file)
        print ('set ylabel "Duty Cycle %"', file=out_file)
        print ('set yrange [-1:101] noextend', file=out_file)
        print ('set grid', file=out_file)
        print ('plot $DutyCycle ls 1 title "Duty Cycle Plot" with lines , ', file=out_file, end='')
        print ('  $lCR ls 4 title "Control Range" with lines , ', file=out_file, end='')
        print ('  $hCR ls 4 notitle with lines , ', file=out_file, end='')
        print ('  $SP ls 3 title "Duty Cycle Set Point" with lines , ', file=out_file, end='')
        print ('  $tPV ls 2 title "Target Temp" with lines , ', file=out_file, end='')
        if not process_value is None :
            print ('  $PV ls 5 title "Measured Temp" with lines ,', file=out_file, end='')
            print ('  $Inter ls 5 dt 5 notitle with lines', file=out_file, end='')
        print ('', file=out_file)

        print ('print "CR to exit"', file=out_file)
        print ('pause -1', file=out_file)
        return duty_cycle

    def __str__ (self) :
        print ("\nCurrent Settings #################")
        print ("Low:", self.control_range_low)
        print ("Target:", self.target_PV)
        print ("High:", self.control_range_high)
        print ("process_value:", self.process_value)

## end YAPController

#------------------------------------------------------------------<<<<<<<
#
if __name__ == "__main__" :
    if MACHINE_FREQ > 0 :
        machine.freq(240000000)
    yap = YAPController (None ,
                      225.0 ,
                      30.0 ,
                      control_range_low = 200.0 ,
                      control_range_high = 235.0)
    #yap.dump ()
    #yap.new_PV (70.0)
    new_duty_cycle = None
    with open ("YAPover1.gnuplot", "w") as fil :
        print (yap.gnuplot (out_file = fil))
    #---- process_value stabilizes at 210 degrees
    with open ("YAPover2.gnuplot", "w") as fil :
        new_duty_cycle = yap.gnuplot (process_value=210, out_file = fil)
    print (new_duty_cycle)
    #---- Adjust duty cycle set point
    yap.set_duty_cycle (new_duty_cycle)
    #---- New plot
    with open ("YAPover3.gnuplot", "w") as fil :
        new_duty_cycle = yap.gnuplot (process_value=210, out_file = fil)
    print (new_duty_cycle)
    print (yap)
        #print (yap.gnuplot (out_file = fil), end='')
