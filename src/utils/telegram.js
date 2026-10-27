/**
 * Telegram Notification Utility
 * Sends non-blocking HTML-formatted alerts to Telegram bot/channel
 */
export async function sendTelegram(message) {
  const token = process.env.TELEGRAM_BOT_TOKEN?.trim();
  const chatId = process.env.TELEGRAM_CHAT_ID?.trim();
  if (!token || !chatId) return false;

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
    return response.ok;
  } catch (err) {
    // Fail safely without affecting downloads
    return false;
  }
}
