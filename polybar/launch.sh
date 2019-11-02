#!/bin/bash

killall -q polybar
while pgrep -u $UID -x polybar >/dev/null; do sleep 1; done

killall -q compton
while pgrep -u $UID -x compton >/dev/null; do sleep 1; done

echo -e "-- begin --\n" | tee -a /tmp/polybar.log | tee -a /tmp/compton.log

polybar top >>/tmp/polybar.log 2>&1 &
compton >>/tmp/compton.log 2>&1 &