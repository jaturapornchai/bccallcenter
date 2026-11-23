// Post-call analysis via Ollama Cloud (DeepSeek latest)

const SYSTEM_PROMPT = `คุณคือผู้เชี่ยวชาญ QA และ Customer Experience (CX) สำหรับระบบ Call Center AI อัตโนมัติของบริษัทไทย
วิเคราะห์บทสนทนาทางโทรศัพท์ระหว่าง "ลูกค้า" กับ "J.A.R.V.I.S" แล้วตอบกลับเป็น JSON object เท่านั้น (ห้ามใส่ markdown code fence) ตามโครงสร้างนี้:
{
  "csatScore": <ตัวเลข 1-10 คะแนนความพึงพอใจของลูกค้า>,
  "aiQualityScore": <ตัวเลข 1-10 คะแนนคุณภาพการตอบของ AI>,
  "sentiment": "<positive | neutral | negative | urgent>",
  "emotionLabel": "<อารมณ์ลูกค้าภาษาไทยสั้นๆ เช่น พึงพอใจมาก, สอบถามปกติ, กังวลเร่งด่วน, หงุดหงิด>",
  "emotionTrend": "<แนวโน้มอารมณ์ เช่น กังวล -> สบายใจขึ้น>",
  "topic": "<หัวข้อ/เจตนาหลักของลูกค้าภาษาไทยสั้นกระชับ>",
  "callerName": "<ชื่อหรือชื่อเล่นของลูกค้าถ้าลูกค้าบอกในสาย เช่น คุณสมชาย, พี่จืด, เบิร์ด ถ้าลูกค้าไม่ได้บอกใส่ค่าว่าง \"\">",
  "callerCompany": "<ชื่อร้าน/บริษัท/ประเภทธุรกิจของลูกค้าถ้าลูกค้าบอก เช่น ร้านวัสดุก่อสร้าง ถ้าไม่ได้บอกใส่ค่าว่าง \"\">",
  "summary": "<สรุปเนื้อหาการสนทนา 2-3 ประโยค ภาษาไทย>",
  "resolved": <true หรือ false ว่าแก้ปัญหาจบในสายหรือไม่>,
  "resolutionSteps": "<สรุปวิธีแก้ไขที่ให้ลูกค้าไปแล้ว หรือแนวทางแก้ไขที่ควรทำ ภาษาไทยชัดเจน>",
  "needsHumanFollowUp": <true หรือ false ว่าควรให้พนักงานคนจริงติดตามต่อหรือไม่>,
  "urgency": "<low | medium | high>",
  "buyingIntent": "<high | medium | low ระดับโอกาสสนใจซื้อหรือทดลองใช้โปรแกรม>",
  "interestedModules": ["<ชื่อโมดูลหรือเรื่องของโปรแกรม BC Ai Account ที่ลูกค้าสนใจ เช่น BC AutoCare, ระบบค่าคอมมิชชั่น, สต็อกหลายสาขา สูงสุด 5 รายการ>"],
  "followUpAction": "<สิ่งที่ทีมงานควรทำต่อเพื่อให้ลูกค้าพึงพอใจสูงสุด ภาษาไทย>",
  "followUpMessage": "<ข้อความสรุปสั้นๆ 3-5 บรรทัด ภาษาไทยสุภาพเป็นกันเอง สำหรับให้แอดมินคัดลอกส่งให้ลูกค้าหลังวางสาย (ขึ้นต้นทักชื่อลูกค้าถ้าทราบ สรุปเรื่อง/โมดูลที่คุยกัน และลงท้ายเชิญชวนสอบถามเพิ่มเติมจากทีม BC Call Center)>",
  "aiCoachingTip": "<ข้อเสนอแนะสำหรับปรับปรุง AI ให้ตอบดีขึ้นในครั้งถัดไป ภาษาไทย>"
}`;

export async function analyzeCallTranscript({ phone, messages, durationSec, baseUrl, model, apiKey }) {
  if (!apiKey || !Array.isArray(messages) || messages.length === 0) {
    return null;
  }

  const cleanBase = (baseUrl || "https://ollama.com").replace(/\/+$/, "");
  const modelName = model || "deepseek-v4.1-flash";

  const transcriptText = messages
    .map((m) => `${m.role === "caller" ? "ลูกค้า" : m.role === "agent" ? "พนักงาน" : "J.A.R.V.I.S"}: ${m.text}`)
    .join("\n");

  const userPrompt = `เบอร์ลูกค้า: ${phone}\nระยะเวลาสนทนา: ${durationSec || 0} วินาที\n\nบทสนทนา:\n${transcriptText}`;

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 35_000);

  try {
    const res = await fetch(`${cleanBase}/api/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        model: modelName,
        stream: false,
        think: false,
        format: "json",
        messages: [
          { role: "system", content: SYSTEM_PROMPT },
          { role: "user", content: userPrompt },
        ],
      }),
      signal: controller.signal,
    });

    if (!res.ok) {
      throw new Error(`Ollama Cloud HTTP ${res.status}`);
    }

    const data = await res.json();
    const rawContent = data?.message?.content || "";
    const jsonText = rawContent
      .replace(/^```json\s*/i, "")
      .replace(/^```\s*/i, "")
      .replace(/\s*```$/i, "")
      .trim();

    const parsed = JSON.parse(jsonText);
    return {
      analyzedAt: new Date().toISOString(),
      model: modelName,
      csatScore: clampScore(parsed.csatScore),
      aiQualityScore: clampScore(parsed.aiQualityScore),
      sentiment: ["positive", "neutral", "negative", "urgent"].includes(parsed.sentiment)
        ? parsed.sentiment
        : "neutral",
      emotionLabel: String(parsed.emotionLabel || "ปกติ"),
      emotionTrend: String(parsed.emotionTrend || "-"),
      topic: String(parsed.topic || "สอบถามทั่วไป"),
      callerName: String(parsed.callerName || "").trim().slice(0, 80),
      callerCompany: String(parsed.callerCompany || "").trim().slice(0, 120),
      summary: String(parsed.summary || ""),
      resolved: Boolean(parsed.resolved),
      resolutionSteps: String(parsed.resolutionSteps || ""),
      needsHumanFollowUp: Boolean(parsed.needsHumanFollowUp),
      urgency: ["low", "medium", "high"].includes(parsed.urgency) ? parsed.urgency : "low",
      buyingIntent: ["high", "medium", "low"].includes(parsed.buyingIntent) ? parsed.buyingIntent : "low",
      interestedModules: Array.isArray(parsed.interestedModules)
        ? parsed.interestedModules.map((m) => String(m || "").trim()).filter(Boolean).slice(0, 6)
        : [],
      followUpAction: String(parsed.followUpAction || ""),
      followUpMessage: String(parsed.followUpMessage || "").trim().slice(0, 1200),
      aiCoachingTip: String(parsed.aiCoachingTip || ""),
    };
  } catch (err) {
    console.warn("[CallAnalyzer] Failed:", err.message);
    return {
      analyzedAt: new Date().toISOString(),
      model: modelName,
      error: err.message,
    };
  } finally {
    clearTimeout(timeout);
  }
}

function clampScore(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return 5;
  return Math.max(1, Math.min(10, Math.round(n)));
}
