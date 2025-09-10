#!/usr/bin/env python3
import sys
import re
from math import sqrt

idle_temp_delta = 65.0
preheat_time = 9.0

trim_comments = re.compile(r';.*$')
command_word = re.compile(r'([A-Z])([0-9.+-]*)$')

# Represents a raw or parsed G-code command, along with associated "facts"
# (computed data about the print state) and "magic" (extra G-code commands
# inserted before or after this one).
class Command(object):
    def __init__(self, raw):
        self.raw = raw.rstrip()
        self.cmd = None
        self.args = {}
        self.facts = {}
        self.magic_pre = []
        self.magic_post = []

        cmd = None
        args = {}
        for word in trim_comments.sub('', self.raw).split():
            match = command_word.match(word)
            if match is None:
                # Found something we can't parse, time to bail
                return
            if cmd is None:
                # Save the command. We still parse it as an arg as well
                # (used in particular for T commands)
                cmd = word
            arg_letter = match.group(1)
            arg_number = match.group(2)
            args[arg_letter] = int(arg_number) if arg_letter == 'T' else float(arg_number)

        self.cmd = cmd
        self.args = args

    def output(self, out):
        for ln in self.magic_pre:
            out.write(ln + ' ; inserted by tool-wizard\n')
        out.write(self.raw + '\n')
        for ln in self.magic_post:
            out.write(ln + ' ; inserted by tool-wizard\n')

    def debug_dump(self, out):
        if len(self.magic_pre) > 0:
            out.write('; magic_pre: {!r}\n'.format(self.magic_pre))
        out.write(self.raw + '\n')
        out.write('; facts: {!r}\n'.format(self.facts))
        if len(self.magic_post) > 0:
            out.write('; magic_post: {!r}\n'.format(self.magic_post))
        out.write('\n')

def parse_file(filename):
    commands = []
    with open(filename) as f:
        for ln in f:
            commands.append(Command(ln))
    return commands

def write_file(filename):
    global commands
    with open(filename, 'w') as f:
        for command in commands:
            command.output(f)

# Invokes a callback for each command in the G-code file, in forward or reverse order.
# The callback receives the command, as well as the facts dict from the previous or next command.
def propagate(callback, reverse=False):
    global commands
    it = reversed(commands) if reverse else iter(commands)

    prev_facts = {}
    for command in it:
        callback(command, prev_facts)
        prev_facts = command.facts

movement_commands = set('G0 G1 G2 G3'.split())

# Propagate XY position and feed rate.
def prop_gcode_state(command, prev_facts):
    for var in 'XYF':
        if command.cmd in movement_commands and var in command.args:
            command.facts[var] = command.args[var]
        else:
            command.facts[var] = prev_facts.get(var)

# Propagate a rough time estimate of when each command will execute.
def prop_time_estimate(command, prev_facts):
    time = prev_facts.get('time', 0.0)

    if prev_facts.get('X') is not None and prev_facts.get('Y') is not None and 'F' in command.facts:
        delta = lambda ax: (command.facts[ax] - prev_facts[ax])**2.0
        dist = sqrt(delta('X') + delta('Y'))
        if dist > 0.0:
            time += dist / command.facts['F'] * 60.0

    command.facts['time'] = time

# Propagate the active tool number.
def prop_active_tool(command, prev_facts):
    command.facts['active_tool'] = prev_facts.get('active_tool')
    if command.cmd is not None and command.cmd.startswith('T'):
        command.facts['active_tool'] = command.args['T']

# Back-propagate the time and temperature of each tool's next use.
def prop_next_needed(command, next_facts):
    command.facts['time_next_needed'] = dict(next_facts.get('time_next_needed', {}))
    command.facts['next_temp'] = dict(next_facts.get('next_temp', {}))

    if command.cmd is not None:
        if command.cmd.startswith('T'):
            command.facts['time_next_needed'][command.args['T']] = command.facts['time']
        elif command.cmd in ('M104', 'M109'):
            command.facts['next_temp'][command.args['T']] = command.args['S']

# Propagate preheat state and emit preheat / heater idle commands.
def prop_preheat(command, prev_facts):
    active_tool = command.facts['active_tool']
    if active_tool is None:
        # Can't do anything if we don't know the active tool yet.
        return

    if 'heating' in prev_facts:
        command.facts['heating'] = set(prev_facts['heating'])
    else:
        # Assume only the active tool is heating at the start.
        command.facts['heating'] = set([active_tool])

    # For every tool that is needed in the future, decide if we should start heating or idling it.
    for tool, time_needed in command.facts['time_next_needed'].items():
        if tool == active_tool:
            # Don't mess with the active tool.
            continue

        time_till_needed = time_needed - command.facts['time']
        next_temp = command.facts['next_temp'][tool]

        if tool not in command.facts['heating'] and time_till_needed <= preheat_time:
            command.facts['heating'].add(tool)
            command.magic_post.append('M104 T{} S{}'.format(tool, next_temp))

        elif tool in command.facts['heating'] and time_till_needed > preheat_time:
            command.facts['heating'].remove(tool)
            command.magic_post.append('M104 T{} S{}'.format(tool, next_temp - idle_temp_delta))

    # Turn off tools that won't be used for the remainder of the print.
    turn_off = list(tool for tool in command.facts['heating'] if tool not in command.facts['time_next_needed'])
    for tool in turn_off:
        if tool != active_tool:
            command.facts['heating'].remove(tool)
            command.magic_post.append('M104 T{} S0'.format(tool))

# Propagate fan speed, and transfer speed to the active tool.
def prop_fan(command, prev_facts):
    fan_speed = prev_facts.get('fan_speed', 0)
    if command.cmd is not None:
        if command.cmd == 'M106':
            fan_speed = command.args['S']
        elif command.cmd == 'M107':
            fan_speed = 0
        elif command.cmd.startswith('T'):
            command.magic_pre.append('M106 S0')
            command.magic_post.append('M106 S{}'.format(fan_speed))

    command.facts['fan_speed'] = fan_speed

filename = sys.argv[1]

commands = parse_file(filename)

propagate(prop_gcode_state)
propagate(prop_time_estimate)
propagate(prop_active_tool)
propagate(prop_next_needed, reverse=True)
propagate(prop_preheat)
propagate(prop_fan)

write_file(filename)
#for command in commands:
#    command.debug_dump(sys.stdout)
