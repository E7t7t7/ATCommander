"""Readable event results and bounded Discord messages."""
import re

def event_results(mode,rows,*,failed=False,stopped=False):
    title=str(mode)+(' paused after an error'if failed else' stopped'if stopped else' completed')
    lines=[title];wins=losses=unknown=0
    for row in rows:
        name=' '.join(str(row.get('opponent')or'Unknown opponent').split())[:100]
        name=re.sub(r'([\\`*_~\[\]])',r'\\\1',name).replace('@','@\u200b')
        won=row.get('won')
        if won is True:wins+=1;label='Win'
        elif won is False:losses+=1;label='Loss'
        else:unknown+=1;label='Unconfirmed'
        lines.append('Vs '+name+' '+label)
    lines.append(f'Total {wins} wins {losses} losses'+(f' · {unknown} unconfirmed'if unknown else''))
    return '\n'.join(lines)

def notification_chunks(text,limit=1800):
    chunks=[];current=''
    for line in str(text).splitlines():
        while len(line)>limit:
            if current:chunks.append(current);current=''
            chunks.append(line[:limit]);line=line[limit:]
        if len(current)+len(line)+bool(current)>limit:
            chunks.append(current);current=line
        else:current+=('\n'if current else'')+line
    if current:chunks.append(current)
    return chunks
