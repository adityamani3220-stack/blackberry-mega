        ])
    )


async def help_command(update, context):
    await update.message.reply_text(
        "╭━━━〔 💎 BLACK BERRY MEGA 〕━━━╮\n"
        "┃ <b>👮 MANAGEMENT</b>\n"
        "┃ /ban /unban /kick /mute /dmute /unmute\n"
        "┃ /warn /unwarn /warns /clear 10\n"
        "┣━━━━━━━━━━━━━━━━━━━━\n"
        "┃ <b>🛡️ PROTECTION</b>\n"
        "┃ /approve /free /unapprove\n"
        "┃ Reply OR @username OR User ID\n"
        "┃ /welcome on|off /antilink on|off\n"
        "┃ /antiflood on|off /badword on|off\n"
        "┃ /filteradd word /filterdel word /filters\n"
        "┃ Reply to response: /filteradd @user | /filteradd @user word\n"
        "┃ User ID filter: /filteradd 123456789 | /filteradd 123456789 word\n"
        "┃ Response can be text, sticker, photo, video, GIF, document or other media\n"
        "┣━━━━━━━━━━━━━━━━━━━━\n"
        "┃ <b>✨ AURA GAME</b>\n"
        "┃ /aura — profile\n"
        "┃ /aurabal — current group Aura balance\n"
        "┃ /auraworldwidebal — worldwide Aura balance\n"
        "┃ /aurabalance — group + worldwide balance\n"
        "┃ /aura give @user 10\n"
        "┃ /aura rob @user 10\n"
        "┃ /aura protect @user\n"
        "┃ /aura unprotect @user\n"
        "┃ /giveaura @user 10\n"
        "┃ /robaura @user 10\n"
        "┃ /auraprotect / /auraunprotect\n"
        "┃ /topaura /globaltopaura\n"
        "┃ /setaura @user 100\n"
        "┃ /aura on|off\n"
        "┣━━━━━━━━━━━━━━━━━━━━\n"
        "┃ <b>📜 HISTORY & USER INFO</b>\n"
        "┃ /userinfo — old names + usernames\n"
        "┃ /history 10 — command history\n"
        "┃ /id /ping /help /adminhelp\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        parse_mode="HTML",
    )


async def adminhelp_command(update, context):
    if not await admin_required(update):
        return
    await help_command(update, context)


# ============================================================
# MAIN
# ============================================================

def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN environment variable is missing.")
        print("Windows CMD: set BOT_TOKEN=YOUR_NEW_BOT_TOKEN")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("adminhelp", adminhelp_command))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("userinfo", userinfo_command))
    app.add_handler(CommandHandler("history", history_command))

    # Approve / Free
    app.add_handler(CommandHandler("approve", approve_command))
    app.add_handler(CommandHandler("free", free_command))
    app.add_handler(CommandHandler("unblock", free_command))
    app.add_handler(CommandHandler("unapprove", unapprove_command))

    # Moderation
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("kick", kick_command))
    app.add_handler(CommandHandler("mute", mute_command))
    app.add_handler(CommandHandler("dmute", dmute_command))
    app.add_handler(CommandHandler("unmute", unmute_command))
    app.add_handler(CommandHandler("warn", warn_command))
    app.add_handler(CommandHandler("unwarn", unwarn_command))
    app.add_handler(CommandHandler("warns", warns_command))
    app.add_handler(CommandHandler("clear", clear_command))

    # Settings
    app.add_handler(CommandHandler("welcome", welcome_command))
    app.add_handler(CommandHandler("antilink", antilink_command))
    app.add_handler(CommandHandler("antiflood", antiflood_command))
    app.add_handler(CommandHandler("badword", badword_command))
    app.add_handler(CommandHandler("filteradd", filter_add_command))
    app.add_handler(CommandHandler("filterdel", filter_remove_command))
    app.add_handler(CommandHandler("filters", filters_command))
    app.add_handler(CommandHandler("aura", aura_command))
    app.add_handler(CommandHandler("aurabalance", aura_balance_command))
    app.add_handler(CommandHandler("aurabal", aura_group_balance_command))
    app.add_handler(CommandHandler("auragroupbal", aura_group_balance_command))
    app.add_handler(CommandHandler("auraworldwidebal", aura_worldwide_balance_command))
    app.add_handler(CommandHandler("aurawbal", aura_worldwide_balance_command))
    app.add_handler(CommandHandler("topaura", topaura_command))
    app.add_handler(CommandHandler("globaltopaura", globaltopaura_command))
    app.add_handler(CommandHandler("setaura", set_aura_command))
    app.add_handler(CommandHandler("giveaura", give_aura_command))
    app.add_handler(CommandHandler("robaura", rob_aura_command))
    app.add_handler(CommandHandler("auraprotect", aura_protect_command))
    app.add_handler(CommandHandler("auraunprotect", aura_unprotect_command))

    # Welcome event
    app.add_handler(ChatMemberHandler(member_update, ChatMemberHandler.CHAT_MEMBER))

    # Clickable profile / Aura buttons
    app.add_handler(CallbackQueryHandler(aura_callback, pattern=r"^(aura_|userinfo_)"))

    # Messages
    app.add_handler(MessageHandler(~filters.COMMAND, message_handler))

    print("💎 Black Berry Mega Bot is running... | Management + Aura + History + Protection")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
