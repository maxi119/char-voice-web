"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const S = { cfg: null, status: null, lib: { characters: [], clips: [] }, selChar: null };

async function api(path, opts = {}) {
  const o = { method: opts.method || (opts.body ? "POST" : "GET"), headers: {} };
  if (opts.body) { o.body = JSON.stringify(opts.body); o.headers["Content-Type"] = "application/json"; }
  const r = await fetch("/api/" + path, o);
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.error) throw new Error(j.error || ("HTTP " + r.status));
  return j;
}
function toast(msg, ms = 2600) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), ms);
}
const fileToB64 = f => new Promise((ok, err) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = err; r.readAsDataURL(f); });
const audioUrl = f => "/audio/" + encodeURI(f);
const splitTags = s => String(s || "").split(/[,，、]/).map(x => x.trim()).filter(Boolean);
function colorOf(id) { let h = 0; for (const c of String(id)) h = (h * 31 + c.charCodeAt(0)) % 360; return `hsl(${h} 55% 45%)`; }

/* ---------------- 分頁 ---------------- */
$$(".tabs button").forEach(b => b.onclick = () => showTab(b.dataset.tab));
function showTab(t) {
  $$(".tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === t));
  $$(".tab").forEach(s => s.classList.toggle("active", s.id === "tab-" + t));
  if (t === "library") renderLibrary();
  if (t === "speak") renderVoiceSelect();
  try { localStorage.setItem("tab", t); } catch (e) {}
}

/* ---------------- 狀態 ---------------- */
async function refreshStatus() {
  const el = $("#status");
  try {
    const s = S.status = await api("status");
    const allNodes = s.nodes && Object.values(s.nodes).every(Boolean);
    if (!s.online) { el.className = "status bad"; el.textContent = "ComfyUI 離線"; }
    else if (!allNodes) { el.className = "status warn"; el.textContent = "缺少 QwenTTS 節點"; }
    else {
      el.className = "status ok";
      const v = s.vram ? `  顯存 ${(s.vram[0] / 1e9).toFixed(1)}/${(s.vram[1] / 1e9).toFixed(1)}G` : "";
      el.textContent = `ComfyUI 已連線${s.queue ? "（佇列 " + s.queue + "）" : ""}${v}・BreezyVoice ${s.breezy ? "✔" : "✖"}`;
    }
    const b = $("#banner");
    if (!s.online) {
      b.innerHTML = `<b>無法連線到 ComfyUI（${esc(s.comfy_url)}）</b><br>請確認 ComfyUI 已啟動並使用 <code>--listen</code>，或到「設定」修改位址。<br><span class="hint">${esc(s.error || "")}</span>`;
      b.classList.remove("hidden");
    } else if (!allNodes) {
      b.innerHTML = `<b>ComfyUI 尚未安裝 Qwen3-TTS 節點</b>，網站需要 <a href="https://github.com/1038lab/ComfyUI-QwenTTS" target="_blank">ComfyUI-QwenTTS</a> 才能生成語音。<br>
        安裝方式（擇一）：<br>① ComfyUI Manager → Custom Nodes Manager → 搜尋 <b>ComfyUI-QwenTTS</b>（作者 1038lab）→ Install → 重新啟動 ComfyUI<br>
        ② 手動：<pre>cd E:\\ComfyUI_windows_portable\\ComfyUI\\custom_nodes
git clone https://github.com/1038lab/ComfyUI-QwenTTS
..\\..\\python_embeded\\python.exe -m pip install -r ComfyUI-QwenTTS\\requirements.txt</pre>
        首次生成會自動下載模型（約 4–8 GB）到 <code>ComfyUI/models/TTS/Qwen3-TTS/</code>。`;
      b.classList.remove("hidden");
    } else b.classList.add("hidden");
    fillLangs(s.languages);
    const ms = $("#s-model"), cur = ms.value;
    ms.innerHTML = (s.model_sizes.length ? s.model_sizes : ["1.7B", "0.6B"]).map(x => `<option>${esc(x)}</option>`).join("");
    ms.value = cur || S.cfg?.clone_model_size || "1.7B";
    const bo = $('#s-engine option[value="breezy"]'); bo.disabled = !s.breezy;
    bo.textContent = "BreezyVoice（台灣腔）" + (s.breezy ? "" : "－未啟動");
    if (!s.breezy && $("#s-engine").value === "breezy") $("#s-engine").value = "qwen";
    $("#d-tw").disabled = !s.breezy; if (!s.breezy) $("#d-tw").checked = false;
    renderVoiceSelect();
  } catch (e) { el.className = "status bad"; el.textContent = "網站後端錯誤"; }
}
const LANG_ZH = { Auto: "自動偵測", Chinese: "中文（國語）", English: "英文", Japanese: "日文", Korean: "韓文", German: "德文", French: "法文", Russian: "俄文", Portuguese: "葡萄牙文", Spanish: "西班牙文", Italian: "義大利文" };
function fillLangs(list) {
  if (!list || !list.length) list = ["Auto", "Chinese", "English", "Japanese", "Korean"];
  $$("select.lang").forEach(sel => {
    const cur = sel.value;
    sel.innerHTML = (sel.id === "s-lang" ? `<option value="">跟隨角色設定</option>` : "") +
      list.map(l => `<option value="${esc(l)}">${esc(LANG_ZH[l] || l)}</option>`).join("");
    if (cur && [...sel.options].some(o => o.value === cur)) sel.value = cur;
    else if (sel.dataset.want && list.includes(sel.dataset.want)) sel.value = sel.dataset.want;
    else if (sel.id !== "s-lang") sel.value = list.includes("Chinese") ? "Chinese" : list[0];
  });
}

/* ---------------- 工作進度 ---------------- */
async function runJob(startPromise, box, btn) {
  box.className = "job"; box.innerHTML = `送出中…<div class="bar indet"><i></i></div>`;
  btn.disabled = true;
  try {
    const { job } = await startPromise;
    box.innerHTML = `<span class="msg">排隊中…</span> <button class="small" style="float:right">取消</button><div class="bar"><i style="width:0"></i></div>`;
    $("button", box).onclick = () => api("jobs/" + job, { method: "DELETE" });
    const t0 = Date.now();
    while (true) {
      await new Promise(r => setTimeout(r, 800));
      const j = await api("jobs/" + job);
      const sec = Math.round((Date.now() - t0) / 1000);
      $(".msg", box).textContent = `${j.message}（${sec} 秒）`;
      const pct = j.total ? Math.max(4, (j.progress / j.total) * 100) : 0;
      $(".bar i", box).style.width = pct + "%";
      $(".bar", box).classList.toggle("indet", j.state !== "running" || j.total <= 1);
      if (j.state === "done") { box.classList.add("hidden"); return j.result; }
      if (j.state === "error") throw new Error(j.error);
    }
  } catch (e) {
    box.className = "job error"; box.textContent = "❌ " + e.message; throw e;
  } finally { btn.disabled = false; }
}

/* ---------------- 設計聲音 ---------------- */
const CHIPS = {
  性別: ["男性", "女性", "中性"],
  年齡: ["小孩", "少年", "少女", "青年", "中年", "老年"],
  音色: ["溫暖", "低沉", "清亮", "沙啞", "甜美", "磁性", "稚嫩", "渾厚", "尖細", "氣聲"],
  情緒: ["平靜", "開朗", "憂鬱", "憤怒", "冷漠", "害羞", "傲慢", "緊張", "慈祥", "陰險"],
  風格: ["語速緩慢", "語速很快", "播音腔", "說書人", "耳語", "動漫感", "戲劇化", "口語自然", "台灣口音"],
};
const EXAMPLES = {
  熱血少年: "十六歲左右的少年，聲音明亮有活力，語氣熱血衝動，說話速度偏快，充滿自信",
  冷靜女特務: "三十歲左右的女性，聲音低沉冷靜，語氣簡潔俐落、不帶情緒，咬字清晰",
  慈祥老爺爺: "七十多歲的老爺爺，聲音溫暖略帶沙啞，語速緩慢，語氣慈祥和藹，偶爾帶笑意",
  反派巫婆: "年老的女巫，聲音尖細沙啞，語氣陰險狡詐，說話時拖長尾音，帶著詭異的笑聲",
  元氣少女: "十幾歲的少女，聲音甜美清亮，語氣活潑開朗，充滿元氣，語調起伏大",
  沉穩旁白: "中年男性旁白，聲音渾厚有磁性，語速平穩，像紀錄片的說書人，富有感染力",
};
$("#d-chips").innerHTML = Object.entries(CHIPS).map(([k, arr]) =>
  `<div class="chipgroup"><b>${k}</b>${arr.map(x => `<span class="chip">${x}</span>`).join("")}</div>`).join("");
$("#d-chips").onclick = e => {
  if (!e.target.classList.contains("chip")) return;
  const ta = $("#d-instruct"), v = ta.value.trim();
  ta.value = v ? v.replace(/[，,。]?$/, "，") + e.target.textContent : e.target.textContent;
  ta.focus();
};
$("#d-examples").innerHTML = Object.keys(EXAMPLES).map(k => `<a data-k="${k}">${k}</a>`).join("");
$("#d-examples").onclick = e => { const k = e.target.dataset.k; if (k) $("#d-instruct").value = EXAMPLES[k]; };
$("#d-temp").oninput = e => $("#d-temp-v").textContent = e.target.value;

$("#d-go").onclick = async () => {
  const body = {
    instruct: $("#d-instruct").value.trim(), text: $("#d-text").value.trim(), language: $("#d-lang").value,
    count: +$("#d-count").value, taiwan: $("#d-tw").checked, seed: $("#d-seed").value === "" ? null : +$("#d-seed").value, temperature: +$("#d-temp").value,
  };
  if (!body.instruct) return toast("請先輸入聲音描述");
  try {
    const r = await runJob(api("design", { body }), $("#d-job"), $("#d-go"));
    renderCands(r);
  } catch (e) {}
};
function renderCands(r) {
  const box = $("#d-cands");
  if (box.querySelector(".empty")) box.innerHTML = "";
  r.candidates.slice().reverse().forEach((c, i) => {
    const d = document.createElement("div");
    d.className = "cand";
    d.innerHTML = `<div class="head"><span><b>候選</b> <span class="hint">種子 ${c.seed}・${c.duration}s</span></span>
      <span><button class="small reuse">↻ 用此種子</button> <button class="small primary save">💾 存為角色</button></span></div>
      <div class="hint" style="margin-bottom:4px">${esc(r.instruct)}</div>
      ${c.tw_file ? `<div class="hint">🇹🇼 BreezyVoice 台灣腔版</div><audio controls src="${audioUrl(c.tw_file)}"></audio><div class="hint">原始 Qwen 版</div>` : ""}
      <audio controls src="${audioUrl(c.file)}"></audio>`;
    $(".reuse", d).onclick = () => { $("#d-seed").value = c.seed; $("#d-count").value = "1"; toast("已填入種子，可修改描述或文字後重新生成"); };
    $(".save", d).onclick = () => saveCharModal({ candidate: c.file, tw_candidate: c.tw_file, seed: c.seed, instruct: r.instruct, description: r.instruct, ref_text: r.text, language: r.language });
    box.prepend(d);
  });
  box.firstElementChild?.querySelector("audio")?.play().catch(() => {});
}
function saveCharModal(base) {
  modal(`<h2>存為角色</h2>
    <label>角色名稱</label><input id="m-name" type="text" placeholder="例：艾琳・冷靜女特務">
    ${base.tw_candidate ? `<label>參考音</label><label style="font-weight:400"><input type="radio" name="m-ref" value="tw" checked> 台灣腔版（推薦）</label><label style="font-weight:400"><input type="radio" name="m-ref" value="orig"> 原始 Qwen 版</label>` : ""}
    <label>描述</label><textarea id="m-desc" rows="3">${esc(base.description || "")}</textarea>
    <label>標籤 <span class="hint">逗號分隔</span></label><input id="m-tags" type="text" placeholder="主角, 女性, 科幻">
    <label>備註</label><textarea id="m-notes" rows="2" placeholder="角色背景、使用情境…"></textarea>
    <div class="actions"><button class="primary" id="m-ok">儲存</button><button id="m-cancel">取消</button></div>`);
  $("#m-name").focus();
  $("#m-ok").onclick = async () => {
    try {
      const useTw = base.tw_candidate && ($('input[name="m-ref"]:checked')?.value || "tw") === "tw";
      if (useTw) base = { ...base, candidate: base.tw_candidate };
      const c = await api("characters", { body: { ...base, name: $("#m-name").value.trim() || "未命名角色", description: $("#m-desc").value, tags: splitTags($("#m-tags").value), notes: $("#m-notes").value } });
      closeModal(); toast(`已建立角色「${c.name}」`); await loadLib(); S.selChar = c.id;
      $("#s-voice").value = "c:" + c.id; renderVoiceSelect();
    } catch (e) { toast("❌ " + e.message); }
  };
  $("#m-cancel").onclick = closeModal;
}
$("#u-go").onclick = async () => {
  const f = $("#u-file").files[0];
  if (!f) return toast("請選擇音檔");
  saveCharModal({ audio_b64: await fileToB64(f), ref_text: $("#u-text").value.trim(), language: $("#u-lang").value, trim: true, description: "上傳錄音：" + f.name });
};

/* ---------------- 台詞生成 ---------------- */
function renderVoiceSelect() {
  const sel = $("#s-voice"), cur = sel.value || (S.selChar ? "c:" + S.selChar : "");
  const chars = S.lib.characters.map(c => `<option value="c:${c.id}">${esc(c.name)}${c.tags?.length ? "　(" + esc(c.tags.join("、")) + ")" : ""}</option>`).join("");
  const spk = (S.status?.speakers || []).map(s => `<option value="s:${esc(s)}">${esc(s)}</option>`).join("");
  sel.innerHTML = `<optgroup label="我的角色">${chars || '<option disabled value="">（尚無角色，請先到「設計聲音」建立）</option>'}</optgroup>` +
    (spk ? `<optgroup label="內建預設音色">${spk}</optgroup>` : "");
  if (cur && [...sel.options].some(o => o.value === cur)) sel.value = cur;
  onVoiceChange();
}
function currentVoice() {
  const v = $("#s-voice").value || "";
  if (v.startsWith("c:")) return { character: S.lib.characters.find(c => c.id === v.slice(2)) };
  if (v.startsWith("s:")) return { speaker: v.slice(2) };
  return {};
}
function onVoiceChange() {
  const v = currentVoice();
  $("#s-speaker-opts").classList.toggle("hidden", !v.speaker);
  $("#s-engine-box").classList.toggle("hidden", !v.character);
  const info = $("#s-char-info");
  if (v.character) {
    S.selChar = v.character.id;
    info.innerHTML = `參考音：${esc(v.character.description || "")}<audio controls preload="none" src="${audioUrl(v.character.ref_audio)}"></audio>`;
  } else info.innerHTML = v.speaker ? "內建音色，可在上方輸入語氣指示。" : "";
  renderRecent();
}
$("#s-voice").onchange = onVoiceChange;
try { const e = localStorage.getItem("engine"); if (e) $("#s-engine").value = e; } catch (e) {}
$("#s-engine").onchange = () => { try { localStorage.setItem("engine", $("#s-engine").value); } catch (e) {} };

function buildToolbar() {
  const tb = $("#s-toolbar"), P = S.cfg.presets;
  tb.innerHTML = Object.entries(P).map(([k, p]) => `<button class="small" data-wrap="${esc(k)}" title="${fxDesc(p)}" style="background:var(--mark-${esc(k)}, var(--panel2))">${esc(p.label || k)}</button>`).join("") +
    `<button class="small" data-custom>🎛 自訂…</button><span class="sep"></span>` +
    [0.3, 0.5, 1].map(s => `<button class="small" data-pause="${s}">⏸ 停${s}秒</button>`).join("") +
    `<span class="sep"></span><button class="small" data-bopo title="BreezyVoice 破音字校正：選取一個字後輸入注音">ㄅ 注音</button><button class="small" data-clear>清除標記</button>`;
}
const fxDesc = p => [p.gain ? `音量${p.gain > 0 ? "+" : ""}${p.gain}dB` : "", p.pitch ? `音高${p.pitch > 0 ? "+" : ""}${p.pitch}` : "", p.tempo && p.tempo != 1 ? `速度×${p.tempo}` : ""].filter(Boolean).join(" ") || "無變化";
function wrapSel(tag) {
  const ta = $("#s-text"), a = ta.selectionStart, b = ta.selectionEnd;
  if (a === b) return toast("請先在台詞中選取要強調的文字");
  const sel = ta.value.slice(a, b);
  ta.setRangeText(`[${tag}]${sel}[/]`, a, b, "end"); ta.focus(); renderPreview();
}
$("#s-toolbar").onclick = e => {
  const b = e.target.closest("button"); if (!b) return;
  const ta = $("#s-text");
  if (b.dataset.wrap) wrapSel(b.dataset.wrap);
  else if (b.dataset.pause) { ta.setRangeText(`[停${b.dataset.pause}]`, ta.selectionStart, ta.selectionEnd, "end"); ta.focus(); renderPreview(); }
  else if (b.dataset.clear) { ta.value = stripMarks(ta.value); renderPreview(); }
  else if ("custom" in b.dataset) customFx();
  else if ("bopo" in b.dataset) bopoFx();
};
function bopoFx() {
  const ta = $("#s-text"), [a, b] = [ta.selectionStart, ta.selectionEnd];
  if (b - a !== 1) return toast("請選取「一個字」再加注音（例如：行）");
  modal(`<h2>注音校正「${esc(ta.value.slice(a, b))}」</h2><p class="hint">只對 BreezyVoice 有效。聲調用數字：1 一聲、2 二聲、3 三聲、4 四聲、5 輕聲。例：ㄒㄧㄥ2、ㄏㄤ2</p>
    <input id="m-bopo" type="text" placeholder="ㄒㄧㄥ2"><div class="actions"><button class="primary" id="m-ok">套用</button><button id="m-cancel">取消</button></div>`);
  $("#m-bopo").focus(); $("#m-cancel").onclick = closeModal;
  $("#m-ok").onclick = () => { const v = $("#m-bopo").value.trim(); closeModal(); if (!v) return; ta.focus(); ta.setRangeText(`${ta.value.slice(a, b)}[:${v}]`, a, b, "end"); renderPreview(); };
}
function customFx() {
  const ta = $("#s-text");
  if (ta.selectionStart === ta.selectionEnd) return toast("請先在台詞中選取要調整的文字");
  const [a, b] = [ta.selectionStart, ta.selectionEnd];
  modal(`<h2>自訂效果</h2><p class="hint">套用到：「${esc(ta.value.slice(a, b))}」</p>
    <label>音量 <b id="cv">+4</b> dB</label><input id="cg" type="range" min="-12" max="12" step="1" value="4">
    <label>音高 <b id="cpv">+2</b> 半音</label><input id="cp" type="range" min="-8" max="8" step="0.5" value="2">
    <label>速度 <b id="csv">0.90</b>×</label><input id="cs" type="range" min="0.5" max="1.8" step="0.05" value="0.9">
    <div class="actions"><button class="primary" id="m-ok">套用</button><button id="m-cancel">取消</button></div>`);
  const sgn = v => (v > 0 ? "+" : "") + v;
  const upd = () => { $("#cv").textContent = sgn(+$("#cg").value); $("#cpv").textContent = sgn(+$("#cp").value); $("#csv").textContent = (+$("#cs").value).toFixed(2); };
  $$("#modal input").forEach(i => i.oninput = upd);
  $("#m-ok").onclick = () => {
    const tag = [`v${sgn(+$("#cg").value)}`, `p${sgn(+$("#cp").value)}`, `s${(+$("#cs").value).toFixed(2)}`].join(" ");
    closeModal(); ta.focus(); ta.setSelectionRange(a, b); wrapSel(tag);
  };
  $("#m-cancel").onclick = closeModal;
}
const MARK_RE = /\[停\s*([\d.]+)\s*秒?\]|\[(?![:：])([^\[\]\/]+)\]([\s\S]*?)\[\/\]/g;
const stripMarks = t => t.replace(MARK_RE, (m, p, tag, txt) => p ? "" : txt);
function renderMarkup(text) {
  let out = "", pos = 0;
  text.replace(MARK_RE, (m, pause, tag, txt, idx) => {
    out += esc(text.slice(pos, idx));
    if (pause) out += `<span class="pause">⏸ ${pause}s</span>`;
    else {
      const first = tag.trim().split(/\s+/)[0];
      out += `<span class="seg" style="background:var(--mark-${esc(first)}, var(--mark-重))">${esc(txt)}<small>${esc(tag)}</small></span>`;
    }
    pos = idx + m.length; return m;
  });
  return (out + esc(text.slice(pos))).replace(/\[[:：]([^\[\]]*)\]/g, '<sup class="hint">$1</sup>');
}
function renderPreview() {
  const t = $("#s-text").value;
  const n = (t.match(MARK_RE) || []).length;
  $("#s-preview").innerHTML = t.trim() ? renderMarkup(t) + (n ? `<div class="hint">共 ${n} 個標記。整句會一次生成（保持語氣自然），再自動定位重點字套用效果；停頓處會分開生成。</div>` : "")
    : '<span class="hint">預覽：標記會以顏色顯示</span>';
}
$("#s-text").oninput = renderPreview;
$("#s-text").onkeydown = e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $("#s-go").click(); } };
$("#s-speed").oninput = e => $("#s-speed-v").textContent = (+e.target.value).toFixed(2) + "×";
$("#s-pitch").oninput = e => $("#s-pitch-v").textContent = e.target.value;
$("#s-gap").oninput = e => $("#s-gap-v").textContent = e.target.value;

$("#s-go").onclick = async () => {
  const v = currentVoice();
  if (!v.character && !v.speaker) return toast("請先選擇聲音來源");
  const body = {
    text: $("#s-text").value, language: $("#s-lang").value || null, speed: +$("#s-speed").value, pitch: +$("#s-pitch").value,
    gap_ms: +$("#s-gap").value, normalize: $("#s-norm").checked, model_size: $("#s-model").value,
    seed: $("#s-seed").value === "" ? null : +$("#s-seed").value, tags: splitTags($("#s-tags").value),
    character_id: v.character?.id, speaker: v.speaker, engine: v.character ? $("#s-engine").value : "qwen", instruct: v.speaker ? $("#s-instruct").value : "",
  };
  if (!body.text.trim()) return toast("請輸入台詞");
  try {
    const r = await runJob(api("speak", { body }), $("#s-job"), $("#s-go"));
    await loadLib();
    const box = $("#s-result"); box.innerHTML = "<h3>結果</h3>"; box.appendChild(clipEl(r.clip, true));
    box.querySelector("audio").play().catch(() => {});
    renderRecent();
  } catch (e) {}
};
function renderRecent() {
  const v = currentVoice(), box = $("#s-recent");
  const list = S.lib.clips.filter(c => v.character ? c.character_id === v.character.id : (v.speaker && c.speaker === v.speaker)).slice(0, 30);
  box.innerHTML = list.length ? "" : '<p class="empty">還沒有片段</p>';
  list.forEach(c => box.appendChild(clipEl(c)));
}

/* ---------------- 片段元件 ---------------- */
function clipEl(c, compact) {
  const d = document.createElement("div");
  d.className = "clip";
  const p = c.params || {};
  const fx = [p.speed && p.speed != 1 ? `語速${p.speed}×` : "", p.pitch ? `音高${p.pitch}` : "", p.instruct ? `「${p.instruct}」` : ""].filter(Boolean).join("・");
  const nm = encodeURIComponent((charName(c) + "_" + (c.plain || "")).slice(0, 30));
  d.innerHTML = `<div class="txt">${renderMarkup(c.text)}</div>
    <audio controls preload="none" src="${audioUrl(c.file)}"></audio>
    <div class="meta"><span>${c.favorite ? '<span class="fav">★</span> ' : ""}${(c.tags || []).map(t => `<span class="tag">${esc(t)}</span>`).join("")}
      ${c.duration}s・${esc(c.created.slice(5, 16))}${fx ? "・" + esc(fx) : ""}${c.note ? "・📝" + esc(c.note) : ""}</span>
    <span class="btns">
      <button class="small" data-a="fav" title="收藏">${c.favorite ? "★" : "☆"}</button>
      <button class="small" data-a="edit" title="載入到編輯器重新生成">✎</button>
      <button class="small" data-a="note" title="備註／標籤">📝</button>
      ${c.character_id ? '<button class="small" data-a="ref" title="把這段設為角色的參考音">🎯</button>' : ""}
      <a class="btn small" href="${audioUrl(c.file)}?name=${nm}" download title="下載 WAV">WAV</a>
      <a class="btn small" href="${audioUrl(c.file)}?format=mp3&name=${nm}" download title="下載 MP3">MP3</a>
      <button class="small danger" data-a="del" title="刪除">🗑</button></span></div>`;
  d.querySelector(".btns").onclick = async e => {
    const a = e.target.closest("button")?.dataset.a; if (!a) return;
    try {
      if (a === "fav") { await api("clips/" + c.id, { method: "PUT", body: { favorite: !c.favorite } }); }
      else if (a === "del") { if (!confirm("刪除這個片段？")) return; await api("clips/" + c.id, { method: "DELETE" }); }
      else if (a === "edit") {
        showTab("speak"); $("#s-voice").value = c.character_id ? "c:" + c.character_id : "s:" + c.speaker; onVoiceChange();
        $("#s-text").value = c.text; if (p.speed) { $("#s-speed").value = p.speed; $("#s-speed").oninput({ target: $("#s-speed") }); }
        if (p.pitch != null) { $("#s-pitch").value = p.pitch; $("#s-pitch-v").textContent = p.pitch; }
        if (p.instruct) $("#s-instruct").value = p.instruct;
        renderPreview(); return;
      }
      else if (a === "note") {
        modal(`<h2>備註與標籤</h2><label>標籤</label><input id="m-tags" type="text" value="${esc((c.tags || []).join(", "))}">
          <label>備註</label><textarea id="m-note" rows="3">${esc(c.note || "")}</textarea>
          <div class="actions"><button class="primary" id="m-ok">儲存</button><button id="m-cancel">取消</button></div>`);
        $("#m-cancel").onclick = closeModal;
        $("#m-ok").onclick = async () => { await api("clips/" + c.id, { method: "PUT", body: { tags: splitTags($("#m-tags").value), note: $("#m-note").value } }); closeModal(); await loadLib(); rerender(); };
        return;
      }
      else if (a === "ref") {
        if (!confirm("用這段語音取代角色的參考音？之後的生成都會以它為音色基準。")) return;
        await api("characters/" + c.character_id, { method: "PUT", body: { ref_from_clip: c.id } }); toast("已更新參考音");
      }
      await loadLib(); rerender();
    } catch (err) { toast("❌ " + err.message); }
  };
  return d;
}
const charName = c => c.character_id ? (S.lib.characters.find(x => x.id === c.character_id)?.name || "角色") : (c.speaker || "");
function rerender() {
  renderRecent();
  if ($("#tab-library").classList.contains("active")) renderLibrary();
  const r = $("#s-result .clip"); if (r) $("#s-result").innerHTML = "";
}

/* ---------------- 聲音庫 ---------------- */
async function loadLib() { S.lib = await api("library"); renderVoiceSelect(); }
function renderLibrary() {
  const q = $("#l-search").value.trim().toLowerCase(), tag = $("#l-tag").value;
  const tags = [...new Set(S.lib.characters.flatMap(c => c.tags || []))];
  const ts = $("#l-tag"); ts.innerHTML = `<option value="">全部標籤</option>` + tags.map(t => `<option>${esc(t)}</option>`).join(""); ts.value = tags.includes(tag) ? tag : "";
  const clipsOf = id => S.lib.clips.filter(c => c.character_id === id);
  const match = c => (!ts.value || (c.tags || []).includes(ts.value)) && (!q ||
    [c.name, c.description, c.notes, ...(c.tags || []), ...clipsOf(c.id).map(x => x.plain)].join(" ").toLowerCase().includes(q));
  const list = S.lib.characters.filter(match);
  const box = $("#l-chars");
  box.innerHTML = list.length ? "" : `<p class="empty">${S.lib.characters.length ? "沒有符合的角色" : "聲音庫是空的。到「設計聲音」建立第一個角色吧！"}</p>`;
  list.forEach(c => {
    const d = document.createElement("div");
    d.className = "char" + (S.selChar === c.id ? " sel" : "");
    d.innerHTML = `<div class="h"><div class="avatar" style="background:${colorOf(c.id)}">${esc(c.name.slice(0, 1))}</div>
      <div><div class="name">${esc(c.name)}</div><div>${(c.tags || []).map(t => `<span class="tag">${esc(t)}</span>`).join("")}</div></div></div>
      <div class="desc">${esc(c.description || "")}</div>
      <audio controls preload="none" src="${audioUrl(c.ref_audio)}"></audio>
      <div class="foot"><span>${clipsOf(c.id).length} 個片段・${esc((c.created || "").slice(0, 10))}</span><button class="small primary">🎙 說話</button></div>`;
    d.onclick = e => {
      if (e.target.closest("audio")) return;
      if (e.target.closest("button")) { S.selChar = c.id; showTab("speak"); $("#s-voice").value = "c:" + c.id; onVoiceChange(); $("#s-text").focus(); return; }
      S.selChar = c.id; renderLibrary();
    };
    box.appendChild(d);
  });
  renderDetail();
}
function renderDetail() {
  const box = $("#l-detail"), c = S.lib.characters.find(x => x.id === S.selChar);
  const orphan = S.lib.clips.filter(x => !x.character_id);
  if (!c) {
    if (!orphan.length) return box.classList.add("hidden");
    box.classList.remove("hidden");
    box.innerHTML = `<h2>內建音色片段</h2><div class="clips"></div>`;
    orphan.forEach(x => $(".clips", box).appendChild(clipEl(x)));
    return;
  }
  box.classList.remove("hidden");
  const clips = S.lib.clips.filter(x => x.character_id === c.id).sort((a, b) => (b.favorite - a.favorite));
  box.innerHTML = `<div class="detail-head"><div class="avatar" style="background:${colorOf(c.id)};width:56px;height:56px;font-size:22px">${esc(c.name.slice(0, 1))}</div>
    <div class="grow"><h2 style="margin-bottom:4px">${esc(c.name)}</h2>
      <div>${esc(c.description || "")}</div>
      ${c.notes ? `<div class="hint" style="margin-top:4px">📝 ${esc(c.notes)}</div>` : ""}
      <div class="hint" style="margin-top:4px">參考音逐字稿：${esc(c.ref_text || "（無，僅複製音色）")}　語言：${esc(LANG_ZH[c.language] || c.language)}　${c.seed != null ? "種子：" + c.seed : ""}</div>
      <audio controls preload="none" src="${audioUrl(c.ref_audio)}" style="margin-top:8px"></audio></div>
    <div class="actions" style="margin:0">
      <button data-a="speak" class="primary">🎙 說話</button><button data-a="edit">✎ 編輯</button>
      <button data-a="redesign" title="以此描述回到設計頁">🎨 重新設計</button>
      <a class="btn" href="/api/export/${c.id}" download>📦 匯出</a><button data-a="del" class="danger">🗑 刪除</button></div></div>
    <hr><h3>片段（${clips.length}）</h3><div class="clips"></div>`;
  clips.forEach(x => $(".clips", box).appendChild(clipEl(x)));
  if (!clips.length) $(".clips", box).innerHTML = '<p class="empty">還沒有片段</p>';
  $(".detail-head .actions", box).onclick = async e => {
    const a = e.target.closest("button")?.dataset.a; if (!a) return;
    if (a === "speak") { showTab("speak"); $("#s-voice").value = "c:" + c.id; onVoiceChange(); }
    if (a === "redesign") { showTab("design"); $("#d-instruct").value = c.instruct || c.description || ""; if (c.seed != null) $("#d-seed").value = c.seed; }
    if (a === "del") {
      if (!confirm(`刪除角色「${c.name}」以及它的 ${clips.length} 個片段？此動作無法復原（可先匯出備份）。`)) return;
      await api("characters/" + c.id, { method: "DELETE" }); S.selChar = null; await loadLib(); renderLibrary();
    }
    if (a === "edit") editCharModal(c);
  };
}
function editCharModal(c) {
  modal(`<h2>編輯角色</h2>
    <label>名稱</label><input id="m-name" type="text" value="${esc(c.name)}">
    <label>描述</label><textarea id="m-desc" rows="3">${esc(c.description || "")}</textarea>
    <label>標籤</label><input id="m-tags" type="text" value="${esc((c.tags || []).join(", "))}">
    <label>備註</label><textarea id="m-notes" rows="2">${esc(c.notes || "")}</textarea>
    <label>參考音逐字稿 <span class="hint">需與參考音內容一致，留空=只複製音色</span></label><textarea id="m-ref" rows="2">${esc(c.ref_text || "")}</textarea>
    <label>預設語言</label><select id="m-lang" class="lang"></select>
    <label>更換參考音檔 <span class="hint">（選填）</span></label><input id="m-file" type="file" accept="audio/*">
    <div class="actions"><button class="primary" id="m-ok">儲存</button><button id="m-cancel">取消</button></div>`);
  fillLangs(S.status?.languages); $("#m-lang").value = c.language || "Auto";
  $("#m-cancel").onclick = closeModal;
  $("#m-ok").onclick = async () => {
    const f = $("#m-file").files[0];
    const body = { name: $("#m-name").value.trim() || c.name, description: $("#m-desc").value, tags: splitTags($("#m-tags").value), notes: $("#m-notes").value, ref_text: $("#m-ref").value.trim(), language: $("#m-lang").value };
    if (f) body.audio_b64 = await fileToB64(f);
    try { await api("characters/" + c.id, { method: "PUT", body }); closeModal(); await loadLib(); renderLibrary(); toast("已儲存"); }
    catch (e) { toast("❌ " + e.message); }
  };
}
$("#l-search").oninput = renderLibrary;
$("#l-tag").onchange = renderLibrary;
$("#l-import").onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  try { const r = await api("import", { body: { zip_b64: await fileToB64(f) } }); toast(`已匯入「${r.character.name}」與 ${r.clips} 個片段`); S.selChar = r.character.id; await loadLib(); renderLibrary(); }
  catch (err) { toast("❌ " + err.message); }
  e.target.value = "";
};

/* ---------------- 設定 ---------------- */
function renderSettings() {
  const c = S.cfg;
  $("#c-url").value = c.comfy_url; $("#c-model").value = c.clone_model_size; $("#c-temp").value = c.temperature;
  $("#c-gap").value = c.segment_gap_ms; $("#c-norm").checked = !!c.normalize;
  $("#c-breezy").value = c.breezy_url || ""; $("#c-accent").value = c.accent_instruct || ""; $("#c-simp").checked = c.simplify_for_tts !== false; $("#c-lang").dataset.want = c.default_language || "Chinese";
  if ([...$("#c-lang").options].some(o => o.value === $("#c-lang").dataset.want)) $("#c-lang").value = $("#c-lang").dataset.want;
  $("#s-gap").value = c.segment_gap_ms; $("#s-gap-v").textContent = c.segment_gap_ms; $("#s-norm").checked = !!c.normalize;
  $("#d-temp").value = c.temperature; $("#d-temp-v").textContent = c.temperature;
  $("#c-presets").innerHTML = Object.entries(c.presets).map(([k, p]) => presetRow(k, p)).join("");
  buildToolbar(); renderPreview();
}
const presetRow = (k, p) => `<tr><td><input value="${esc(k)}" size="3" data-f="key"></td><td><input value="${esc(p.label || "")}" data-f="label"></td>
  <td><input type="number" step="1" value="${p.gain}" data-f="gain"></td><td><input type="number" step="0.5" value="${p.pitch}" data-f="pitch"></td>
  <td><input type="number" step="0.05" value="${p.tempo}" data-f="tempo"></td><td><button class="small danger" onclick="this.closest('tr').remove()">✕</button></td></tr>`;
$("#c-addp").onclick = () => $("#c-presets").insertAdjacentHTML("beforeend", presetRow("新", { label: "新效果", gain: 0, pitch: 0, tempo: 1 }));
$("#c-save").onclick = async () => {
  const presets = {};
  $$("#c-presets tr").forEach(tr => {
    const g = f => $(`[data-f=${f}]`, tr).value;
    const k = g("key").trim().replace(/[\[\]\/\s]/g, "");
    if (k) presets[k] = { label: g("label") || k, gain: +g("gain") || 0, pitch: +g("pitch") || 0, tempo: +g("tempo") || 1 };
  });
  try {
    S.cfg = await api("config", { method: "PUT", body: { comfy_url: $("#c-url").value.trim(), clone_model_size: $("#c-model").value, temperature: +$("#c-temp").value, segment_gap_ms: +$("#c-gap").value, normalize: $("#c-norm").checked, presets, accent_instruct: $("#c-accent").value.trim(), breezy_url: $("#c-breezy").value.trim(), simplify_for_tts: $("#c-simp").checked, default_language: $("#c-lang").value } });
    renderSettings(); toast("設定已儲存"); refreshStatus();
  } catch (e) { toast("❌ " + e.message); }
};
$("#c-free").onclick = async () => { try { await api("free", { body: {} }); toast("已要求 ComfyUI 釋放顯存"); setTimeout(refreshStatus, 1500); } catch (e) { toast("❌ " + e.message); } };

/* ---------------- 對話框 ---------------- */
function modal(html) { $("#modal-box").innerHTML = html; $("#modal").classList.remove("hidden"); }
function closeModal() { $("#modal").classList.add("hidden"); }
$("#modal").onclick = e => { if (e.target.id === "modal") closeModal(); };
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });

/* ---------------- 啟動 ---------------- */
(async function init() {
  S.cfg = await api("config");
  renderSettings();
  await loadLib();
  await refreshStatus();
  setInterval(refreshStatus, 15000);
  let t = "design"; try { t = localStorage.getItem("tab") || t; } catch (e) {}
  showTab(t);
})();
