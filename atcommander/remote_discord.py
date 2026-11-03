"""Discord gateway inside the desktop app. Bot token never enters settings/logs."""
import asyncio
import json
import threading

class DiscordConnection:
    def __init__(self,inbox,emit,orders_config=None):
        self.orders_config=dict(orders_config or {});self.inbox=inbox;self.emit=emit;self.loop=None;self.client=None;self.thread=None;self._stop=threading.Event();self.ready=False
    def start(self):
        self.thread=threading.Thread(target=self._work,daemon=True,name='Commander Discord');self.thread.start()
    def _work(self):
        try:asyncio.run(self._run())
        except ImportError:self.emit({'kind':'discord_status','text':'Discord support is missing. Use Install Discord support.'})
        except Exception:self.emit({'kind':'discord_status','text':'Discord connection failed. Check the saved bot token and bot permissions.'})
        finally:self.ready=False
    async def _run(self):
        import discord
        from discord import app_commands
        from .cm_discord_bot import _load_token,ReviewCache,_truncate
        token=_load_token(False)
        if not token:
            self.emit({'kind':'discord_status','text':'Save a bot token first.'});return
        self.loop=asyncio.get_running_loop();intents=discord.Intents.default();intents.message_content=self.orders_config.get('enabled')is True
        client=discord.Client(intents=intents,allowed_mentions=discord.AllowedMentions.none());self.client=client
        tree=app_commands.CommandTree(client);group=app_commands.Group(name='commander',description='Control your AT Commander PC')
        async def request(interaction,action,target='',arguments=''):
            try:future=self.inbox.submit(interaction.user.id,interaction.id,action,target,arguments)
            except (ValueError,PermissionError)as error:
                await interaction.response.send_message(str(error),ephemeral=True);return
            await interaction.response.defer(ephemeral=True)
            try:answer=await asyncio.wait_for(asyncio.wrap_future(future),timeout=20)
            except asyncio.TimeoutError:answer='Commander did not accept this request in time. Check /commander status before trying again; an already accepted action may still be running.'
            await interaction.followup.send(str(answer)[:1900],ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
        @group.command(name='status',description='Show active work, connection and event timers')
        async def status(interaction:discord.Interaction):await request(interaction,'status')
        @group.command(name='run',description='Run an event using the deck and targets saved in Commander')
        @app_commands.choices(event=[app_commands.Choice(name=m,value=m)for m in ('Siege','Rumble')])
        async def run(interaction:discord.Interaction,event:str):await request(interaction,'run',event)
        @group.command(name='stop',description='Stop automation and disarm event timers')
        async def stop(interaction:discord.Interaction):await request(interaction,'stop')
        @group.command(name='command',description='Run a registered Commander command; purchases/destructive commands stay on PC')
        async def command(interaction:discord.Interaction,name:str,arguments:str=''):await request(interaction,'command',name,arguments)
        @group.command(name='timer',description='Arm or disarm a Siege/Rumble timer in minutes')
        @app_commands.choices(event=[app_commands.Choice(name=m,value=m)for m in ('Siege','Rumble')],day=[app_commands.Choice(name=d,value=d)for d in ('Saved','Every day','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')])
        async def timer(interaction:discord.Interaction,event:str,enabled:bool,minutes:app_commands.Range[int,1,1440]=30,day:str='Saved'):
            names=('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')
            weekday='saved'if day=='Saved'else names.index(day)if day in names else None
            await request(interaction,'timer',event,json.dumps({'minutes':minutes,'weekday':weekday})if enabled else 'off')
        @group.command(name='help',description='Show phone commands')
        async def help_command(interaction:discord.Interaction):await request(interaction,'help')
        tree.add_command(group)
        cache=ReviewCache()
        async def cm_request(interaction,query=None):
            if interaction.user.id!=self.inbox.owner_id:
                await interaction.response.send_message('This bot is private to its configured owner.',ephemeral=True);return
            await interaction.response.defer(ephemeral=True)
            def review():
                cache.ensure()
                if query:
                    from .community_consensus import fetch_reddit_consensus
                    result=cache.engine.review_name(query);cache.engine.apply_community_consensus(result,fetch_reddit_consensus(result.name));return result.compact()
                cache.refresh();return '\n\n'.join(r.compact()for r in cache.engine.review_shop(cache.offers)[:6])or'Live CM offers are currently unavailable.'
            try:answer=await asyncio.to_thread(review)
            except Exception:answer='CM review is unavailable. Check Commander on your PC.'
            await interaction.followup.send(_truncate(answer),ephemeral=True)
        @tree.command(name='cm',description='Review a combo mastery offer')
        async def cm(interaction:discord.Interaction,combo:str):await cm_request(interaction,combo)
        @tree.command(name='cmshop',description='Review detected live CM offers')
        async def cmshop(interaction:discord.Interaction):await cm_request(interaction)
        @client.event
        async def on_ready():
            self.ready=True
            self.emit({'kind':'discord_status','text':'Discord connected. Phone commands: /commander help','application_id':str(client.application_id or client.user.id)})
        @client.event
        async def on_disconnect():
            self.ready=False;self.emit({'kind':'discord_status','text':'Discord disconnected; reconnecting while Commander is open.'})
        async def setup():
            await tree.sync()
        client.setup_hook=setup
        async def watch():
            while not self._stop.is_set():await asyncio.sleep(.25)
            await client.close()
        watcher=asyncio.create_task(watch())
        try:
            if not self._stop.is_set():await client.start(token)
        finally:watcher.cancel();await client.close()
    def fetch_orders(self):
        if not self.ready or self.loop is None or not self.orders_config.get('enabled'):raise ValueError('Enable guild reading and reconnect Discord first.')
        async def read():
            from datetime import datetime,timezone,timedelta
            from .guild_orders import GUILD_ID,ATTACK_CHANNEL,DEFENSE_CHANNEL,MAX_AGE,DEFENSE_MAX_AGE
            result={}
            for label,cid in (('attack',ATTACK_CHANNEL),('defense',DEFENSE_CHANNEL)):
                records=[]
                try:
                    channel=self.client.get_channel(cid)or await self.client.fetch_channel(cid)
                    if getattr(getattr(channel,'guild',None),'id',None)!=GUILD_ID:raise ValueError('Unexpected guild channel.')
                    async for message in channel.history(limit=100,after=datetime.now(timezone.utc)-timedelta(seconds=MAX_AGE if label=='attack'else DEFENSE_MAX_AGE)):
                        parts=[message.content or '']
                        for embed in message.embeds:
                            parts.extend([embed.title or '',embed.description or ''])
                            for field in embed.fields:parts.extend([field.name+(': 'if not field.name.rstrip().endswith(':')else ''),field.value])
                        text='\n'.join(parts)
                        if len(text)>24000:continue
                        records.append({'text':text,'at':message.created_at.timestamp(),'author_id':str(message.author.id),
                            'author_name':message.author.name,'bot':message.author.bot,'url':message.jump_url})
                except Exception:result[label+'_error']=True
                result[label]=records
            return result
        return asyncio.run_coroutine_threadsafe(read(),self.loop)

    def close(self):
        self._stop.set();self.inbox.close();self.ready=False
    def notify(self,text):
        if not self.ready or self.loop is None or self.loop.is_closed():return
        async def send():
            try:
                user=self.client.get_user(self.inbox.owner_id)or await self.client.fetch_user(self.inbox.owner_id)
                await user.send(str(text)[:1800])
            except Exception:self.emit({'kind':'discord_notice_failed'})
        asyncio.run_coroutine_threadsafe(send(),self.loop)
