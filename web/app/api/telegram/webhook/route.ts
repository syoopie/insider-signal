import { neon } from "@neondatabase/serverless";
import { NextResponse } from "next/server";

/**
 * Telegram webhook. Maintains `telegram_subscribers`, the list the Python
 * alerter fans out to, so people can subscribe by messaging the bot or adding
 * it to a group instead of someone editing TELEGRAM_CHAT_ID and redeploying.
 *
 * Authenticated with the secret Telegram echoes back in
 * X-Telegram-Bot-Api-Secret-Token, set by scripts/register_telegram_webhook.py.
 * Without TELEGRAM_WEBHOOK_SECRET the route refuses every request rather than
 * defaulting open, the same way /api/revalidate does — an unauthenticated
 * subscriber writer is a free way to spam the alert list.
 *
 * This is the one deliberate exception to "web never writes to the database".
 * The write client is instantiated here, in the route, and not exported: put it
 * in lib/ and the next person can import a write path into a page. lib/db.ts
 * stays read-only.
 */

const connectionString = process.env.DATABASE_URL;
const sql = connectionString ? neon(connectionString) : null;

const TELEGRAM_API = "https://api.telegram.org";

type TelegramChat = {
  id: number;
  type: string;
  title?: string;
  username?: string;
  first_name?: string;
};

type TelegramUpdate = {
  update_id: number;
  message?: { chat: TelegramChat; text?: string };
  my_chat_member?: { chat: TelegramChat; new_chat_member: { status: string } };
};

const JOINED_STATUSES = new Set(["member", "administrator"]);
const LEFT_STATUSES = new Set(["left", "kicked"]);

export async function POST(request: Request) {
  const secret = process.env.TELEGRAM_WEBHOOK_SECRET;

  if (!secret) {
    return NextResponse.json({ error: "webhook is not configured" }, { status: 503 });
  }

  const provided = request.headers.get("x-telegram-bot-api-secret-token") ?? "";

  if (!timingSafeEqual(provided, secret)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  try {
    const update = (await request.json()) as TelegramUpdate;

    if (update.my_chat_member) {
      const { chat, new_chat_member } = update.my_chat_member;
      if (JOINED_STATUSES.has(new_chat_member.status)) {
        await subscribe(chat);
      } else if (LEFT_STATUSES.has(new_chat_member.status)) {
        await unsubscribe(chat.id);
      }
      return NextResponse.json({ ok: true });
    }

    if (update.message?.text) {
      const { chat } = update.message;
      const command = update.message.text.trim().toLowerCase();

      if (command === "/subscribe" || command === "/start") {
        await subscribe(chat);
        await reply(chat.id, "You're subscribed — you'll get BUY and CLUSTER_BUY alerts here.");
      } else if (command === "/unsubscribe" || command === "/stop") {
        await unsubscribe(chat.id);
        await reply(chat.id, "Unsubscribed — no more alerts here.");
      } else {
        await reply(chat.id, "Send /subscribe to get BUY and CLUSTER_BUY alerts here.");
      }
    }
  } catch (err) {
    // Telegram retries anything that is not a 200, and it retries hard. A
    // transient database blip must not turn into a retry storm.
    console.error("[telegram-webhook]", err);
  }

  return NextResponse.json({ ok: true });
}

function chatTitle(chat: TelegramChat): string | null {
  return chat.title ?? chat.username ?? chat.first_name ?? null;
}

async function subscribe(chat: TelegramChat) {
  if (!sql) throw new Error("DATABASE_URL is not set");
  await sql.query(
    `INSERT INTO telegram_subscribers (chat_id, chat_type, title)
     VALUES ($1, $2, $3)
     ON CONFLICT (chat_id) DO UPDATE SET
       active = true,
       chat_type = excluded.chat_type,
       title = excluded.title,
       updated_at = now()`,
    [chat.id, chat.type, chatTitle(chat)],
  );
}

/**
 * Update, never insert: someone who was never subscribed and then blocked the
 * bot should leave no row behind.
 */
async function unsubscribe(chatId: number) {
  if (!sql) throw new Error("DATABASE_URL is not set");
  await sql.query(
    "UPDATE telegram_subscribers SET active = false, updated_at = now() WHERE chat_id = $1",
    [chatId],
  );
}

async function reply(chatId: number, text: string) {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  if (!token) return;
  try {
    await fetch(`${TELEGRAM_API}/bot${token}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text }),
    });
  } catch (err) {
    // The subscription is the thing that matters; a missed confirmation is not
    // worth failing the update over.
    console.error("[telegram-webhook] reply failed:", err);
  }
}

/**
 * Constant-time comparison. `===` on secrets leaks their length and prefix
 * through timing; this is cheap enough that there is no reason not to.
 */
function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}
