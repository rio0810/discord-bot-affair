import discord
from discord.ext import commands
from discord import app_commands

from core.admin_base import AdminCogBase

# ルール本文（Discordの見出し/サブテキスト記法をそのまま利用）
RULES_TEXT = """\
┎┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┒
# 🌿ミモザシティ🌿へようこそ！
┖┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┚


## サーバーの基本ルール
### 1. 新人期間について
- サーバーに加入して1週間は `新人`ロールが付与され、**個通や一部機能が制限**されます。
- 1週間の間にトラブルを起こした場合やクレームが多い場合は**BAN**となることがありますので、お気をつけください。

### 2. アカウントについて
- 初期アイコン、及び公序良俗に反するアイコンや名前の使用を禁止します。
- 事情によりサブアカウントを入れたい場合は、運営に連絡をお願いします。

### 3. 個通について
- サーバー外での**裏個通を禁止**します。
- **DMや通話上での個通のお誘いを禁止**します。個通を行いたい場合は**個通申請所**でお願いします。
- ブロックされていることが分かっても、DMやVC上で理由などを詮索することはやめるようお願いします。

### 4. 情報管理について
- サーバー内で知り得た情報を、本人の同意なく第三者と共有することを禁止します。
- 写真や動画等の保存、音声の録音・録画も禁止します。

### 5. メンバー間の交流について
- 他者への批判・否定、暴力的な発言は禁止です。
- 他人の交友関係に干渉しないようにしましょう。
- オフ会の連絡は**不要**です。
-# ※オフ会でのトラブルは自己責任でお願いします。

### 6. 勧誘行為の禁止について
- 他サーバーの運営に関わっている方の参加や、勧誘行為や宣伝活動を禁止します。

### 7. 恋愛について
- `恋愛`ロールがついている方のみ**恋愛可**とします。
- `雑談`ロールがついている方への**アプローチ等は禁止**です。
- **恋愛⇔雑談ロールの変更**のチャンネルでロールの切り替えが可能です。
-# ※一度切り替えると2週間切り替えが出来なくなるのでお気をつけ下さい。

### 8. トラブルの相談について
- 基本的な個人間トラブルは、**当事者での解決**をお願いします。
- 当人同士で解決できないトラブルは、管理人に相談しメンバー間での自治行為はおやめ下さい。
- 通報があった場合、関係者との面談による事実確認を行います。必要に応じて管理者権限の行使や運営からの個別DMでのご連絡を行う場合がありますので、ご協力をお願いします。"""


class Rules(commands.Cog):
    """サーバールールを embed で掲示するコマンド。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_any_role(AdminCogBase.ADMIN_ROLE_ID)
    @app_commands.command(name="rules", description="【管理者専用】サーバールールをembedで掲示します")
    async def rules(self, interaction: discord.Interaction):
        embed = discord.Embed(
            description=RULES_TEXT,
            color=discord.Color.from_str("#F5D547"),  # ミモザ（黄色）
        )
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("ルールを掲示しました。", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Rules(bot))
