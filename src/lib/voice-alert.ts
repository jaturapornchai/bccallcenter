"use client";

import type { CrmTicket } from "@/types/sip";

const THAI_DIGITS = ["ศูนย์", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า"];

/** แปลงเบอร์โทรเป็นคำอ่านไทยทีละหลัก เพื่อไม่ให้เบราว์เซอร์อ่านตัวเลขเป็นหลักล้าน */
export function formatPhoneForSpeech(phone: string): string {
  const digits = String(phone || "").replace(/\D/g, "");
  if (!digits) return "";
  return digits
    .split("")
    .map((d) => THAI_DIGITS[Number(d)] ?? d)
    .join(" ");
}

function cleanTopicForSpeech(subject: string): string {
  return String(subject || "")
    .replace(/^ขอให้โทรกลับ(?:\s*\([^)]*\))?\s*[:：\-]?\s*/i, "")
    .replace(/^ลูกค้าขอให้พนักงานโทรกลับ\s*/i, "")
    .trim()
    .slice(0, 65);
}

/** สร้างข้อความเสียงพูดภาษาไทยสำหรับแจ้งเตือน Ticket โทรกลับ (รองรับโหมดผู้ชมที่ปกปิดข้อมูล) */
export function buildTicketSpeechText(ticket: CrmTicket, readOnly: boolean): string {
  const cbPhone = ticket.callbackPhone || ticket.phone || "";
  const merged = Math.max(1, Number(ticket.mergedCount || 1));
  if (readOnly || cbPhone.includes("•")) {
    return merged > 1
      ? `แจ้งเตือนครับ มีเคสลูกค้าขอให้โทรกลับอัปเดตเพิ่มเติม รวม ${merged} ครั้ง กรุณาตรวจสอบรายการบนหน้าจอครับ`
      : "แจ้งเตือนครับ มีเคสลูกค้าขอให้โทรกลับใหม่ กรุณาตรวจสอบรายการบนหน้าจอครับ";
  }
  const cleanName = String(ticket.customerName || "").trim();
  const namePart = cleanName && !cleanName.includes("ปกปิด") ? ` คุณ${cleanName}` : "";
  const spokenPhone = formatPhoneForSpeech(cbPhone);
  const phonePart = spokenPhone ? ` เบอร์ ${spokenPhone}` : "";
  const mergedPart = merged > 1 ? ` ติดต่อซ้ำรวม ${merged} ครั้ง` : "";
  const topic = cleanTopicForSpeech(ticket.subject);
  const topicPart = topic ? ` เรื่อง ${topic}` : "";
  return `แจ้งเตือนครับ มีลูกค้าขอให้โทรกลับ${namePart}${phonePart}${mergedPart}${topicPart} กรุณากดรับทราบหรือปิดเคสครับ`.slice(0, 190);
}

function pickThaiVoice(synth: SpeechSynthesis): SpeechSynthesisVoice | null {
  try {
    const voices = synth.getVoices();
    if (!voices || voices.length === 0) return null;
    const thaiVoices = voices.filter((v) => String(v.lang || "").toLowerCase().startsWith("th"));
    if (thaiVoices.length === 0) return null;
    return (
      thaiVoices.find((v) => /google|microsoft|niwat|premwadee/i.test(v.name)) ||
      thaiVoices[0]
    );
  } catch {
    return null;
  }
}

/** พูดแจ้งเตือนเคสโทรกลับด้วย Web Speech API (ภาษาไทย) */
export function speakTicketAlert(
  ticket: CrmTicket,
  readOnly: boolean,
  onBlocked?: () => void
): boolean {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return false;
  try {
    const nav = navigator as Navigator & { userActivation?: { hasBeenActive: boolean } };
    if (nav.userActivation && !nav.userActivation.hasBeenActive) {
      return false;
    }
    const synth = window.speechSynthesis;
    const utter = new SpeechSynthesisUtterance(buildTicketSpeechText(ticket, readOnly));
    utter.lang = "th-TH";
    utter.rate = 1.0;
    utter.pitch = 1.0;
    utter.volume = 1.0;
    const voice = pickThaiVoice(synth);
    if (voice) utter.voice = voice;
    if (onBlocked) {
      utter.onerror = (ev) => {
        if (ev.error === "not-allowed") onBlocked();
      };
    }
    synth.cancel();
    synth.speak(utter);
    return true;
  } catch {
    return false;
  }
}

export function stopTicketSpeech(): void {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  try {
    window.speechSynthesis.cancel();
  } catch {}
}

export function sendBrowserTicketNotification(ticket: CrmTicket, readOnly: boolean): void {
  if (typeof window === "undefined" || !("Notification" in window)) return;
  if (Notification.permission !== "granted") return;
  try {
    const body =
      readOnly || (ticket.callbackPhone || ticket.phone || "").includes("•")
        ? `รหัสเคส ${ticket.id} — มีลูกค้าขอให้โทรกลับ`
        : [
            ticket.customerName ? `คุณ${ticket.customerName}` : "",
            ticket.callbackPhone || ticket.phone || "",
            ticket.subject || "",
          ]
            .filter(Boolean)
            .join(" • ");
    new Notification("แจ้งเตือนลูกค้าขอให้โทรกลับ", {
      body,
      tag: ticket.id,
    });
  } catch {}
}
