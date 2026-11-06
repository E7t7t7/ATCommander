"""Small, explicit owner-DM grammar. Never executes arbitrary message text."""
import json
import re
from .remote_control import DAYS
from .guild_orders import attack_order

def parse_dm(text):
    raw=str(text or'').strip()
    if not raw or len(raw)>12000:raise ValueError('Send help for commands, or paste one Siege order post at a time.')
    cleaned=raw.lstrip('!/').strip()
    if cleaned.lower().startswith('commander '):cleaned=cleaned[10:].lstrip()
    first,_,rest=cleaned.partition(' ');word=first.lower();rest=rest.strip()
    if word in {'help','status','stop'}:
        if rest or '\n'in first:raise ValueError('Send one command at a time.')
        return word,'',''
    if word in {'siege','rumble'}and not rest:return 'run',word.title(),''
    if word=='run'and rest.lower()in {'siege','rumble'}:return 'run',rest.title(),''
    if word in {'attack','defense','defence'}and rest:return 'orders','defense'if word!='attack'else'attack',rest
    if word=='command':
        name,_,args=rest.partition(' ')
        if not name:raise ValueError('Example: command play-status')
        return 'command',name,args
    if word in {'cm','cmshop'}:
        if word=='cm'and not rest:raise ValueError('Example: cm Wingnut Amy')
        return 'cm',rest if word=='cm'else'',''
    if word=='timer':
        parts=rest.split()
        if len(parts)<2 or parts[0].lower()not in {'siege','rumble'}:raise ValueError('Use timer siege Monday 30, timer rumble daily 30, or timer siege off.')
        mode=parts[0].title();day=parts[1].title()
        if day=='Off'and len(parts)==2:return 'timer',mode,'off'
        if day=='Daily':weekday=None
        elif day in DAYS[1:]:weekday=DAYS.index(day)-1
        else:raise ValueError('Choose a weekday, daily, or off.')
        if len(parts)>3:raise ValueError('Send one timer command at a time.')
        try:minutes=int(parts[2])if len(parts)==3 else 30
        except ValueError:raise ValueError('Timer minutes must be a whole number.')from None
        if not 1<=minutes<=1440:raise ValueError('Timer minutes must be between 1 and 1440.')
        return 'timer',mode,json.dumps({'minutes':minutes,'weekday':weekday})
    if re.search(r'\bisland\s*:',raw,re.I):return 'orders','defense',raw
    # Accept the human-written Sunday announcement without requiring a prefix.
    try:attack_order(raw)
    except ValueError:raise ValueError('Could not identify that command or order. Send help, or prefix pasted orders with attack / defense.')from None
    return 'orders','attack',raw
