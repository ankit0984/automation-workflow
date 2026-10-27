/**
 * Telegram Notification Utility
 * Sends non-blocking HTML-formatted alerts to Telegram bot/channel
 */
export async function sendTelegram(message) {
  const token = process.env.TELEGRAM_BOT_TOKEN?.trim();
  const chatId = process.env.TELEGRAM_CHAT_ID?.trim();

  if (!token || !chatId) {
    console.log(`[Telegram] Skipping notification: TELEGRAM_BOT_TOKEN is ${token ? 'SET' : 'MISSING'}, TELEGRAM_CHAT_ID is ${chatId ? 'SET' : 'MISSING'}`);
    return false;
  }

  try {
    const response = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_id: chatId,
        text: message,
        parse_mode: 'HTML',
        disable_web_page_preview: true
      })
    });

    if (!response.ok) {
      const errText = await response.text();
      console.warn(`[Telegram] API error (HTTP ${response.status}): ${errText}`);
      return false;
    }

    console.log(`[Telegram] Notification delivered successfully.`);
    return true;
  } catch (err) {
    console.warn(`[Telegram] Network warning: ${err.message}`);
    return false;
  }
}
