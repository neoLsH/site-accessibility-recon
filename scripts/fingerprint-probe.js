/**
 * fingerprint-probe.js — 浏览器运行时指纹快照（含 CDP 泄漏探针）
 *
 * 在页面上下文执行，回答两个问题：
 *   1) 各项身份是否互相自洽（UA / 平台 / Client Hints / WebGL / 时区 / 语言 / 几何）
 *   2) 自动化客户端是否留下了可被页面探测的痕迹
 *
 * 用法：
 *   浏览器控制台：await fingerprintProbe()
 *   CDP Runtime.evaluate：expression = "(async () => JSON.stringify(await fingerprintProbe()))()"
 *                        awaitPromise = true, returnByValue = true
 *
 * 说明：
 *   - 输出用于 diff，不追求“分数”。检测站点只能验证已知向量，不能代表目标站。
 *   - 自洽是必要条件，不是充分条件；能否通过目标站必须回到目标站做活体对照。
 *   - 连续跑两次：canvas / audio 哈希应稳定。同一环境两次不同，通常是噪声补丁过度。
 *   - WebRTC 只统计候选类型，不记录地址本身，避免把内网 IP 写进报告。
 *   - 手法与判据见 references/fingerprint-methods.md。
 */

async function fingerprintProbe() {
  const out = {};
  const nav = navigator;

  // --- 基础身份 ---------------------------------------------------------
  out.ua = nav.userAgent;
  out.platform = nav.platform;
  out.webdriver = nav.webdriver;
  out.language = nav.language;
  out.languages = nav.languages ? Array.from(nav.languages) : null;
  out.hardwareConcurrency = nav.hardwareConcurrency;
  out.deviceMemory = nav.deviceMemory;
  out.maxTouchPoints = nav.maxTouchPoints;
  out.pluginsCount = nav.plugins ? nav.plugins.length : null;
  out.mimeTypesCount = nav.mimeTypes ? nav.mimeTypes.length : null;
  out.hasChromeObject = typeof window.chrome !== 'undefined';

  if (nav.userAgentData) {
    out.uaData = {
      brands: nav.userAgentData.brands,
      mobile: nav.userAgentData.mobile,
      platform: nav.userAgentData.platform,
    };
  }

  out.permissionState = await (async () => {
    try {
      const st = await nav.permissions.query({ name: 'notifications' });
      return st.state;
    } catch {
      return null;
    }
  })();

  // --- 环境 -------------------------------------------------------------
  out.screen = {
    width: screen.width,
    height: screen.height,
    availWidth: screen.availWidth,
    availHeight: screen.availHeight,
    outerWidth: outerWidth,
    outerHeight: outerHeight,
    innerWidth: innerWidth,
    innerHeight: innerHeight,
    devicePixelRatio: devicePixelRatio,
  };
  out.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  out.locale = Intl.DateTimeFormat().resolvedOptions().locale;
  out.tzOffsetMinutes = new Date().getTimezoneOffset();

  // --- 图形栈 -----------------------------------------------------------
  out.webgl = (() => {
    try {
      const c = document.createElement('canvas');
      const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
      if (!gl) return { supported: false };
      const dbg = gl.getExtension('WEBGL_debug_renderer_info');
      return {
        supported: true,
        vendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
        renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
      };
    } catch (e) {
      return { supported: false, error: String(e) };
    }
  })();

  out.canvasHash = (() => {
    try {
      const c = document.createElement('canvas');
      c.width = 240;
      c.height = 60;
      const ctx = c.getContext('2d');
      ctx.textBaseline = 'top';
      ctx.font = '14px Arial';
      ctx.fillStyle = '#f60';
      ctx.fillRect(100, 1, 62, 20);
      ctx.fillStyle = '#069';
      ctx.fillText('fingerprint-probe', 2, 15);
      ctx.fillStyle = 'rgba(102,204,0,0.7)';
      ctx.fillText('fingerprint-probe', 4, 17);
      const data = c.toDataURL();
      let h = 0x811c9dc5;
      for (let i = 0; i < data.length; i++) {
        h ^= data.charCodeAt(i);
        h = (h * 0x01000193) >>> 0;
      }
      return h.toString(16);
    } catch (e) {
      return null;
    }
  })();

  // --- 字体 / 音频 / WebRTC ---------------------------------------------
  // 字体用「宽度差分」：同一段文字在 <family>,monospace 与 monospace 下宽度不同，
  // 说明该字体真实存在且被渲染器采用。
  out.fonts = (() => {
    try {
      const probe = 'mmmmmmmmmmlliWWW0123456789';
      const c = document.createElement('canvas');
      const ctx = c.getContext('2d');
      const width = (family) => {
        ctx.font = `40px ${family}, monospace`;
        return ctx.measureText(probe).width;
      };
      const base = width('monospace');
      const candidates = [
        'Arial', 'Helvetica', 'Times New Roman', 'Courier New', 'Verdana',
        'Georgia', 'Tahoma', 'Trebuchet MS', 'Impact', 'Comic Sans MS',
        'Segoe UI', 'Microsoft YaHei', 'PingFang SC', 'Hiragino Sans GB',
        'Noto Sans CJK SC', 'Source Han Sans SC', 'WenQuanYi Micro Hei',
        'DejaVu Sans', 'Liberation Sans', 'Roboto', 'Ubuntu',
      ];
      const found = candidates.filter((f) => width(`"${f}"`) !== base);
      return { total: found.length, names: found, baseWidth: base };
    } catch (e) {
      return null;
    }
  })();

  out.audioHash = await (async () => {
    try {
      const Ctx = window.OfflineAudioContext || window.webkitOfflineAudioContext;
      if (!Ctx) return null;
      const ctx = new Ctx(1, 44100, 44100);
      const osc = ctx.createOscillator();
      osc.type = 'triangle';
      osc.frequency.value = 10000;
      const comp = ctx.createDynamicsCompressor();
      osc.connect(comp);
      comp.connect(ctx.destination);
      osc.start(0);
      const buf = await ctx.startRendering();
      const data = buf.getChannelData(0);
      let sum = 0;
      for (let i = 4500; i < 5000; i++) sum += Math.abs(data[i]);
      return sum.toFixed(7);
    } catch (e) {
      return null;
    }
  })();

  // 只统计候选类型，不记录地址本身，避免把内网 IP 写进报告
  out.webrtc = await (async () => {
    const PC = window.RTCPeerConnection || window.webkitRTCPeerConnection;
    if (!PC) return { supported: false };
    return new Promise((resolve) => {
      const stats = { supported: true, total: 0, host: 0, mdns: 0, srflx: 0 };
      let pc;
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        try { pc && pc.close(); } catch { /* ignore */ }
        resolve(stats);
      };
      const timer = setTimeout(finish, 1500);
      try {
        pc = new PC({ iceServers: [] });
        pc.createDataChannel('probe');
        pc.onicecandidate = (e) => {
          if (!e.candidate) {
            clearTimeout(timer);
            return finish();
          }
          const c = e.candidate.candidate || '';
          stats.total += 1;
          if (/ typ host/.test(c)) stats.host += 1;
          if (/\.local/.test(c)) stats.mdns += 1;
          if (/ typ srflx/.test(c)) stats.srflx += 1;
        };
        pc.createOffer()
          .then((o) => pc.setLocalDescription(o))
          .catch(() => { clearTimeout(timer); finish(); });
      } catch {
        clearTimeout(timer);
        resolve({ supported: false });
      }
    });
  })();

  out.colorDepth = typeof screen !== 'undefined' ? screen.colorDepth : null;
  out.pixelDepth = typeof screen !== 'undefined' ? screen.pixelDepth : null;
  out.pluginNames = nav.plugins ? Array.from(nav.plugins).map((p) => p.name).slice(0, 10) : null;

  // --- CDP 泄漏探针 -----------------------------------------------------
  // 页面可以通过在 Error.stack 上埋 getter 探测客户端是否启用了 Runtime 域：
  // 启用后，console 事件生成参数预览时会被迫访问该 getter。
  out.cdp = await (async () => {
    const result = { stackAccessed: false, proxyTrapped: false };
    try {
      const err = new Error('probe');
      Object.defineProperty(err, 'stack', {
        configurable: true,
        get() {
          result.stackAccessed = true;
          return 'probe';
        },
      });
      console.debug(err);
    } catch {
      /* 某些引擎的 stack 不可重定义，忽略 */
    }
    try {
      const p = new Proxy(
        {},
        {
          ownKeys(target) {
            result.proxyTrapped = true;
            return Reflect.ownKeys(target);
          },
        }
      );
      console.debug(p);
    } catch {
      /* ignore */
    }
    await new Promise((r) => setTimeout(r, 120));
    return result;
  })();

  // --- 自洽性检查 -------------------------------------------------------
  const warnings = [];
  const ua = out.ua || '';
  const plat = out.platform || '';
  const rr = (out.webgl && out.webgl.renderer) || '';
  const uaPlatform = out.uaData && out.uaData.platform;

  if (/Windows NT/.test(ua) && !/Win/.test(plat)) warnings.push('UA 声称 Windows，但 navigator.platform 不是 Win*');
  if (/Macintosh|Mac OS X/.test(ua) && !/Mac/.test(plat)) warnings.push('UA 声称 macOS，但 navigator.platform 不是 Mac*');
  if (/Android/.test(ua) && /Win/.test(plat)) warnings.push('UA 声称 Android，但 platform 是 Windows');
  if (uaPlatform && plat && !plat.startsWith(uaPlatform.slice(0, 2))) {
    warnings.push(`User-Agent Client Hints 平台(${uaPlatform})与 navigator.platform(${plat})不一致`);
  }
  if (/Direct3D|D3D11/.test(rr) && /Macintosh|Mac OS X/.test(ua)) {
    warnings.push('WebGL 报 Windows 图形栈(Direct3D)，但 UA 声称 macOS');
  }
  if (/Metal|Apple/.test(rr) && /Windows NT/.test(ua)) {
    warnings.push('WebGL 报 Apple 图形栈，但 UA 声称 Windows');
  }
  if (out.screen) {
    const s = out.screen;
    if (!(s.width >= s.outerWidth && s.outerWidth >= s.innerWidth)) {
      warnings.push('屏幕几何不成立：应满足 screen.width ≥ outerWidth ≥ innerWidth');
    }
    if (!(s.height >= s.outerHeight && s.outerHeight >= s.innerHeight)) {
      warnings.push('屏幕几何不成立：应满足 screen.height ≥ outerHeight ≥ innerHeight');
    }
  }
  if (nav.webdriver === true) warnings.push('navigator.webdriver === true');
  if (out.pluginsCount === 0) warnings.push('navigator.plugins 为空');
  if (out.cdp.stackAccessed) warnings.push('CDP 泄漏：Error.stack getter 被访问（客户端可能启用了 Runtime 域）');
  if (out.cdp.proxyTrapped) warnings.push('CDP 泄漏：Proxy ownKeys 被访问');
  if (/@posix/.test(out.language || '')) warnings.push('navigator.language 含 @posix（Linux 容器 locale 特征）');
  if (out.maxTouchPoints === 0 && /Android|iPhone/.test(ua)) warnings.push('移动 UA 但 maxTouchPoints 为 0');

  const uaMain = String(out.language || '').split('-')[0];
  const locMain = String(out.locale || '').split('-')[0];
  if (uaMain && locMain && uaMain !== locMain) {
    warnings.push(`Intl locale(${out.locale})与 navigator.language(${out.language}) 主语言不一致`);
  }

  const uaVer = (ua.match(/Chrome\/(\d+)/) || [])[1];
  const brand = ((out.uaData && out.uaData.brands) || []).find((b) =>
    /Google Chrome|Chromium/.test(b.brand)
  );
  if (uaVer && brand && brand.version && brand.version !== uaVer) {
    warnings.push(`UA 版本(${uaVer})与 Client Hints 版本(${brand.version})不一致`);
  }

  if (typeof out.colorDepth === 'number' && out.colorDepth < 24) {
    warnings.push(`screen.colorDepth=${out.colorDepth}（低于 24，常见于虚拟显示）`);
  }
  if (out.fonts && out.fonts.total === 0) {
    warnings.push('未探测到任何常见字体（字体枚举被削弱或环境异常）');
  }
  if (out.webrtc && out.webrtc.supported && out.webrtc.host > 0 && out.webrtc.mdns === 0) {
    warnings.push('WebRTC host 候选未使用 mDNS，可能暴露内网地址');
  }

  out.coherenceWarnings = warnings;
  return out;
}

if (typeof window !== 'undefined') {
  window.__fingerprintProbe = fingerprintProbe;
}
