from __future__ import annotations

import asyncio, getpass, os, time
from typing import Any

from .api import ATClient
from .credentials import load_credentials
from .cm_reviews import collect_live_review_data
from .community_consensus import fetch_reddit_consensus

TOKEN_SERVICE="ATCommander"
TOKEN_USER="discord-bot-token"


def _load_token(interactive:bool=True)->str:
    token=os.environ.get("ATCOMMANDER_DISCORD_TOKEN","").strip()
    if token:return token
    try:
        import keyring
        token=(keyring.get_password(TOKEN_SERVICE,TOKEN_USER) or "").strip()
        if token:return token
        if interactive:
            print("No Discord bot token is saved yet.")
            token=getpass.getpass("Paste the Discord bot token (input hidden): ").strip()
            if token:keyring.set_password(TOKEN_SERVICE,TOKEN_USER,token)
            return token
    except Exception:
        if interactive:return getpass.getpass("Discord bot token (input hidden): ").strip()
    return ""


class ReviewCache:
    def __init__(self):self.engine=None;self.offers=[];self.loaded=0.0
    def refresh(self):
        self.engine,self.offers,_=collect_live_review_data(ATClient(load_credentials(),timeout=30));self.loaded=time.time()
    def ensure(self):
        if self.engine is None or time.time()-self.loaded>600:self.refresh()


def _truncate(s:str,n:int=1900)->str:
    return s if len(s)<=n else s[:n-3]+"..."


async def run_bot():
    try:
        import discord
        from discord.ext import commands
    except ImportError:
        raise SystemExit("discord.py is not installed. Run Start_CM_Discord_Bot.bat from the AT Commander folder.")
    token=_load_token(True)
    if not token:raise SystemExit("No Discord bot token supplied.")
    intents=discord.Intents.default();intents.message_content=True
    bot=commands.Bot(command_prefix="!",intents=intents,help_command=None)
    cache=ReviewCache()

    async def review_text(query:str)->str:
        def work():
            cache.ensure()
            review=cache.engine.review_name(query)
            consensus=fetch_reddit_consensus(review.name)
            cache.engine.apply_community_consensus(review,consensus)
            text=review.compact()
            if review.community_sources:
                text += "\n\nRecent community evidence:\n" + "\n".join("• "+x for x in review.community_sources[:3])
            elif review.community_note:
                text += "\n\nCommunity check: "+review.community_note
            return text
        return await asyncio.to_thread(work)

    async def shop_text()->str:
        def work():
            cache.refresh()
            if not cache.offers:return "AT Commander could not identify live CM shop rows in the current AT payload. Use !cm <combo name> for an individual review."
            rows=cache.engine.review_shop(cache.offers)
            if not rows:return "Live CM offers were detected, but none mapped cleanly to current combo cards."
            return "**Current CM review**\n\n"+"\n\n".join(r.compact() for r in rows[:8])
        return await asyncio.to_thread(work)

    @bot.event
    async def on_ready():
        print(f"AT Commander CM bot online as {bot.user}")
        print("Use /cm, !cm, or mention the bot followed by a combo name.")
        try:await bot.tree.sync()
        except Exception as e:print(f"Slash-command sync skipped: {e}")

    @bot.event
    async def on_message(message:discord.Message):
        if message.author.bot:return
        # Natural Discord workflow requested by the user: @ding the bot followed
        # by a combo name. Prefix commands still pass through below.
        try:
            if bot.user and bot.user.mentioned_in(message):
                query=(message.content or "").replace(f"<@{bot.user.id}>","").replace(f"<@!{bot.user.id}>","").strip()
                if query.lower().startswith("cm "):query=query[3:].strip()
                if query.lower() in {"shop","cmshop","cm shop"}:
                    await message.channel.send(_truncate(await shop_text()))
                elif query:
                    try:await message.channel.send(_truncate(await review_text(query)))
                    except Exception as e:await message.channel.send(_truncate(f"CM review failed: {e}"))
                else:
                    await message.channel.send("Mention me with a combo name, e.g. `@AT Commander Wingnut Amy`, or use `/cm`.")
        finally:
            await bot.process_commands(message)

    @bot.command(name="cm")
    async def cm_prefix(ctx:commands.Context,*,combo:str=""):
        if not combo:return await ctx.send("Usage: `!cm Combo Name`")
        try:await ctx.send(_truncate(await review_text(combo)))
        except Exception as e:await ctx.send(_truncate(f"CM review failed: {e}"))

    @bot.command(name="cmshop")
    async def cmshop_prefix(ctx:commands.Context):
        try:await ctx.send(_truncate(await shop_text()))
        except Exception as e:await ctx.send(_truncate(f"CM shop review failed: {e}"))

    @bot.command(name="cmhelp")
    async def cmhelp_prefix(ctx:commands.Context):
        await ctx.send("`!cm <combo name>` — review one combo\n`!cmshop` — review detected live CM shop\n`@AT Commander <combo name>` — mention the bot naturally\nSlash commands `/cm` and `/cmshop` are also registered when Discord allows sync.")

    @bot.tree.command(name="cm",description="Review an Animation Throwdown combo mastery offer")
    async def cm_slash(interaction:discord.Interaction,combo:str):
        await interaction.response.defer()
        try:await interaction.followup.send(_truncate(await review_text(combo)))
        except Exception as e:await interaction.followup.send(_truncate(f"CM review failed: {e}"))

    @bot.tree.command(name="cmshop",description="Review the live Animation Throwdown CM shop")
    async def cmshop_slash(interaction:discord.Interaction):
        await interaction.response.defer()
        try:await interaction.followup.send(_truncate(await shop_text()))
        except Exception as e:await interaction.followup.send(_truncate(f"CM shop review failed: {e}"))

    await bot.start(token)


def main():
    asyncio.run(run_bot())

if __name__=="__main__":main()
