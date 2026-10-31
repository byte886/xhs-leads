// lib/extract.js —— 页面数据提取（DOM 层，避开签名问题）
// 与内容脚本共享：搜索页/笔记页/用户页提取逻辑

function xhsExtractSearchNotes(root = document) {
  // 小红书搜索页笔记卡片：section.note-item（data-note-id + xsec_token 链接）
  const cards = [...root.querySelectorAll('section.note-item')];
  return cards.map((c) => {
    // 链接：带 xsec_token 的详情链接（/explore/ 或 /search_result/ 前缀）
    const a = c.querySelector('a.cover[href], a[href*="xsec_token"]') || c.querySelector('a[href*="/explore/"], a[href*="/search_result/"]');
    const href = a ? a.getAttribute('href') : null;
    const id = c.getAttribute('data-note-id') || (href ? (href.match(/[0-9a-f]{24}/) || [])[0] || null : null);
    const img = c.querySelector('img.cover') || c.querySelector('img');
    const authorEl = c.querySelector('.author');
    const likesEl = c.querySelector('.like-wrapper .count, .like-wrapper span, .footer [class*="like"], [class*="like"] .count');
    // 标题：新版搜索卡片独立 .title 元素；旧版 .desc/.note-desc 兜底；再兜底封面 alt
    const titleEl = c.querySelector('.title') || c.querySelector('.desc, .note-desc, [class*="desc"]');
    // 视频标记：视频笔记卡片有播放图标（play-icon，实测可靠），图文没有 → note_type 精确区分
    const playEl = c.querySelector('[class*="play"]');
    return {
      id,
      href: href ? (href.startsWith('/') ? 'https://www.xiaohongshu.com' + href : href) : null,
      title: (titleEl ? titleEl.innerText.trim() : '') || (img ? img.alt || '' : ''),
      author: authorEl ? authorEl.innerText.trim() : '',
      img: img ? (img.src || img.getAttribute('data-src')) : null,
      likes: likesEl ? likesEl.innerText.trim() : '',
      type: playEl ? 'video' : 'note',
    };
  }).filter((x) => x.id || x.href);
}

function xhsExtractNoteDetail(root = document) {
  // 笔记详情页：标题/正文/作者/图片/互动数据
  // 标题来源优先级：og:title（SEO 元数据，实测可靠）→ #detail-title。
  // 注意：绝不回退到 .title/h1 —— 详情页右侧"相关推荐"流里有大量 .title/h1 文本，
  // 会抓到与笔记无关的推荐标题（实测污染源）。
  const ogTitleEl = root.querySelector('meta[property="og:title"]');
  const ogTitle = ogTitleEl ? (ogTitleEl.content || '').trim() : '';
  const titleEl = root.querySelector('#detail-title');
  const title = (ogTitle ? ogTitle.replace(/\s*-\s*(小红书)?\s*$/, '').trim() : '')
    || (titleEl ? titleEl.innerText.trim() : '');
  const descEl = root.querySelector('#detail-desc, .desc, .note-text');
  const authorEl = root.querySelector('.author-wrapper .name, .user-name, .author .name');
  const dateEl = root.querySelector('#date, .date, .publish-time');
  // 正文容器（主图/视频判断的作用域；评论区、相关推荐、头像均不在此容器内）
  const contentRoot = root.querySelector(
    '#detail-container, .note-content, .note-container, [class*="note-content"], [class*="note-container"], [class*="detail-container"]'
  );
  // 视频判断（收紧）：只认正文容器内的 video/播放器，或 SEO og:video 元数据。
  // 绝不使用全文档 [class*="player"] —— 相关推荐流里的视频卡片 class 也含 player，会把图文笔记误判成视频（用户实测 bug）。
  const videoEl = (contentRoot ? contentRoot.querySelector('video, [class*="video-player"]') : null)
    || root.querySelector('video[src], video source[src], meta[property="og:video"], meta[name="og:video"]');
  // 视频地址：video 元素 src / source src / og:video meta content（供视频克隆下载原视频）
  let videoUrl = '';
  try {
    if (videoEl) {
      if (videoEl.tagName === 'META') {
        videoUrl = (videoEl.getAttribute('content') || '').trim();
      } else if (typeof videoEl.src === 'string') {
        videoUrl = videoEl.src.trim();
      } else if (videoEl.querySelector) {
        const srcEl = videoEl.querySelector('source[src]');
        if (srcEl) videoUrl = (srcEl.getAttribute('src') || '').trim();
      }
    }
    if (!videoUrl) {
      const ogVideo = root.querySelector('meta[property="og:video"]');
      if (ogVideo) videoUrl = (ogVideo.getAttribute('content') || '').trim();
    }
  } catch (e) { /* 拿不到视频地址就空 */ }
  // 主图：小红书笔记正文轮播图。官方主图 class 为 note-slider-img（社区爬虫实证：所有笔记图
  // 均含此属性，评论区图/头像/推荐区不含，天然隔离）。仅当主图区为空时，才在正文容器内
  // 兜底找 .swiper-wrapper/.swiper-slide 图（避免抓到评论区/推荐区的轮播图）。
  // URL 规范化去重：小红书同一张图可能带不同 imageView2 参数/时间戳 → 去掉 query/hash 后视为同一张，
  // 解决"两张一模一样"的重复图问题。
  const IMG_HOST = /sns-webpic|sns-img|sns-preview/i;
  const IMG_EXCLUDE = /avatar|fe-static|formula-static|logo|qrcode|svg/i;
  const norm = (u) => {
    try {
      const p = new URL(u);
      p.search = '';
      p.hash = '';
      return p.href.replace(/\/$/, '');
    } catch {
      return (u || '').split('?')[0].replace(/\/$/, '');
    }
  };
  let imgNodes = [...root.querySelectorAll('img.note-slider-img')];
  if (!imgNodes.length) {
    const scope = contentRoot || root;
    imgNodes = [...scope.querySelectorAll('.swiper-wrapper img, .swiper-slide img')];
  }
  const seen = new Set();
  const imgs = imgNodes
    .map((i) => i.src || i.getAttribute('data-src') || null)
    .filter((u) => u && IMG_HOST.test(u))                 // 仅笔记图床域名
    .filter((u) => !IMG_EXCLUDE.test(u))                 // 排除头像/静态资源/logo/二维码/svg
    .filter((u) => {
      const k = norm(u);
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    })
    .slice(0, 9);
  // 互动数据：SSR 注入的 window.__INITIAL_STATE__.note.noteDetailMap[noteId].note
  // 含 interactInfo{likedCount/collectedCount/commentCount/shareCount} + time(毫秒) + user.userId，
  // 无需签名接口，比 DOM 抓取更可靠（新版详情页互动计数不在静态 DOM 文本中）。
  let interact = null, pubTimeMs = null, noteId = null, authorUserId = null, authorNick = '';
  try {
    const st = window.__INITIAL_STATE__ || {};
    const detailMap = (st.note && st.note.noteDetailMap) || {};
    let cur = null;
    const m = location.href.match(/[0-9a-f]{24}/);
    const key = m ? m[0] : null;
    if (key && detailMap[key]) cur = detailMap[key].note;
    else {
      const k = Object.keys(detailMap).find((x) => detailMap[x] && detailMap[x].note && detailMap[x].note.noteId);
      if (k) cur = detailMap[k].note;
    }
    if (cur) {
      interact = cur.interactInfo || null;
      pubTimeMs = cur.time || null;
      noteId = cur.noteId || null;
      authorUserId = (cur.user && cur.user.userId) || null;
      authorNick = (cur.user && cur.user.nickname) || '';
    }
  } catch (e) { /* 拿不到就走空值 */ }
  // 浏览量：详情页头部 "xx次浏览"（DOM 文本；SSR interactInfo 无 views 字段）
  let views = '';
  try {
    const vEl = root.querySelector('[class*="view-count"], [class*="views"], [class*="viewCount"]');
    if (vEl) views = vEl.innerText.trim();
    if (!views) {
      const bodyText = (root.body ? root.body.innerText : '');
      const m = bodyText.match(/([\d.]+[万千wW]?)\s*次浏览/);
      if (m) views = m[1];
    }
  } catch (e) { /* 空值 */ }
  return {
    title,
    desc: descEl ? descEl.innerText.trim() : '',
    author: authorEl ? authorEl.innerText.trim() : '',
    date: dateEl ? dateEl.innerText.trim() : '',
    imgs,
    type: videoEl ? 'video' : 'note',
    video_url: videoUrl,
    note_id: noteId,
    user_id: authorUserId,
    author_nick: authorNick,
    liked_count: interact && interact.likedCount != null ? String(interact.likedCount) : '',
    collected_count: interact && interact.collectedCount != null ? String(interact.collectedCount) : '',
    comment_count: interact && interact.commentCount != null ? String(interact.commentCount) : '',
    shared_count: interact && interact.shareCount != null ? String(interact.shareCount) : '',
    views,
    publish_time_ms: pubTimeMs,
  };
}

function xhsExtractUser(root = document) {
  // 用户主页：昵称/小红书号/简介/关注数/粉丝数/获赞数
  const nickEl = root.querySelector('.user-name, .nickname, .name, .username');
  const idEl = root.querySelector('.user-id, .red-id, .xiaohongshu-id, .user-screen-name');
  const descEl = root.querySelector('.user-desc, .desc, .introduction');
  // 新版统计区（实测 2026-10）：.user-interactions > .clickable，文本形如 "5314\n粉丝"（数字在前标签在后）
  let fans = '', following = '', likes = '';
  const interactions = root.querySelector('.user-interactions');
  if (interactions) {
    for (const c of interactions.querySelectorAll('.clickable')) {
      const t = (c.innerText || '').trim();
      const m = t.match(/([\d.]+[万千wW]?)\s*\n?\s*(关注|粉丝|获赞与收藏)/);
      if (!m) continue;
      const label = m[2];
      if (label === '关注') following = m[1];
      else if (label === '粉丝') fans = m[1];
      else if (label === '获赞与收藏') likes = m[1];
    }
  }
  // 旧版兜底：class 直接含 fans/follower/like 的计数元素
  if (!fans) {
    const f = root.querySelector('[class*="fans"] .count, [class*="follower"] .count, [class*="followers"] .count');
    if (f) fans = f.innerText.trim();
  }
  if (!following) {
    const fo = root.querySelector('[class*="follow"] .count, [class*="following"] .count');
    if (fo) following = fo.innerText.trim();
  }
  if (!likes) {
    const lk = root.querySelector('[class*="like"] .count, [class*="interaction"] .count');
    if (lk) likes = lk.innerText.trim();
  }
  const counts = [...root.querySelectorAll('.count, .num, .stat, [class*="count"]')].map((e) => e.innerText.trim()).filter(Boolean).slice(0, 6);
  return {
    nickname: nickEl ? nickEl.innerText.trim() : '',
    userId: idEl ? idEl.innerText.trim() : '',
    desc: descEl ? descEl.innerText.trim() : '',
    fans,
    following,
    likes,
    counts,
  };
}

function xhsExtractComments(root = document) {
  // 评论区互动用户提取（P4 采集客户）：昵称 + UID + 评论文本
  // 只在该页面的评论容器内提取（避免把侧边栏"我/首页/关注/作者徽标"等非评论链接当成评论者）；
  // 昵称 a 有文本、头像 a 无文本 → 只取有文本的 a，且必须匹配到评论文本才计入。
  const NAV = new Set(['首页', '点点', 'RED', '直播', '发布', '通知', '消息', '我', '关注', '作者', '更多', '关于我们', '返回顶部', '全部', '最热', '最新']);
  const AREA = new Set(['广东', '北京', '上海', '天津', '重庆', '江苏', '浙江', '四川', '湖南', '湖北', '福建', '山东', '河南', '河北', '安徽', '江西', '陕西', '山西', '辽宁', '吉林', '黑龙江', '云南', '贵州', '广西', '海南', '甘肃', '青海', '宁夏', '新疆', '西藏', '内蒙古', '香港', '澳门', '台湾', '日本', '美国', '加拿大', '英国', '法国', '德国', '澳大利亚', '新加坡', '韩国', '泰国', '马来西亚']);
  const TIME = /^(刚刚|昨天|前天|\d+天前|\d+-\d+|\d+小时前|\d+分钟前)$/;
  const out = [];
  const seen = new Set();
  // 评论区精确容器：新版页面评论用户链 author-wrapper < author-container < interaction-container；
  // 容器 class 随改版可能失效，容器内 profile 链接过少时回退全文档
  // （靠 NAV 导航词 + 评论文本存在性过滤，防止把作者/相关推荐/徽标当评论者）
  let container = root.querySelector('.interaction-container') || root.querySelector('[class*="comment"]');
  const probe = container ? [...container.querySelectorAll('a[href*="/user/profile/"]')] : [];
  if (probe.length < 1) container = null;
  const anchors = [...(container || root).querySelectorAll('a[href*="/user/profile/"]')];
  for (const a of anchors) {
    const m = (a.getAttribute('href') || '').match(/\/user\/profile\/([0-9a-f]{24})/);
    if (!m) continue;
    const uid = m[1];
    const nick = (a.innerText || '').trim();
    if (!nick || NAV.has(nick)) continue;
    // 向上找最近的评论条，取第一条像评论文本的内容（排除时间/IP/纯数字/导航词）；同时收集属地与时间
    let cont = a.parentElement;
    let comment = '';
    let ipLocation = '';
    let commentTime = '';
    for (let i = 0; i < 5 && cont; i++) {
      const spans = [...cont.querySelectorAll('span, div, p')]
        .map((e) => e.innerText.trim())
        .filter((t) => t && t.length > 1 && t !== nick);
      const cand = spans.find((t) => (
        t.length >= 2
        && !/^\d+$/.test(t)
        && !TIME.test(t)
        && !NAV.has(t)
        && !AREA.has(t)
      ));
      if (!ipLocation) {
        const ip = spans.find((t) => AREA.has(t));
        if (ip) ipLocation = ip;
      }
      if (!commentTime) {
        const tm = spans.find((t) => TIME.test(t));
        if (tm) commentTime = tm;
      }
      if (cand) {
        const first = cand.split('\n')[0].trim();
        if (first === nick) continue;                    // 作者徽标条目（无独立评论文本）
        if (/^\d+-\d+/.test(first) || TIME.test(first) || AREA.has(first)) continue; // 图片评论（无文字，首行即时间/IP）
        comment = cand;
        break;
      }
      cont = cont.parentElement;
    }
    if (!comment) continue; // 无评论文本 = 非评论者（导航/关注/作者徽标）
    const key = uid + '|' + nick + '|' + comment.slice(0, 24);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      uid,
      nickname: nick,
      comment: comment.slice(0, 300),
      ip_location: ipLocation,
      comment_time: commentTime,
      profile: 'https://www.xiaohongshu.com/user/profile/' + uid,
    });
  }
  return out;
}

// 登录态检测：页面特征（不依赖签名 API）
// 判定收紧（R-6.5 修复）：未登录态小红书 explore 同样渲染推荐流，
// 旧「推荐流兜底 → true」会把未登录页误判为已登录（占位 web_session 注入后假阳性）。
// 现规则：显式命中未登录 UI 特征 → false；仅顶栏头像/用户菜单 → true。
function xhsIsLoggedIn(root = document) {
  const url = location.href;
  if (/website-login|captcha|login/i.test(url)) return false;
  if (root.querySelector('.login-container, .login-modal, [class*="login-modal"]')) return false;
  // 顶栏「登录」按钮 / 登录表单区
  if (root.querySelector('header [class*="login"], [class*="login-button"], [class*="login-btn"]')) return false;
  const t = root.body ? root.body.innerText : '';
  // 未登录标配：搜索栏占位文案「登录探索更多内容」/ 登录+注册引导
  if (t.includes('登录探索更多内容')) return false;
  if (t.includes('登录') && t.includes('注册')) return false;
  // 已登录特征：头像/用户菜单（唯一 true 来源）
  return !!root.querySelector('.avatar, .user-menu, [class*="user-avatar"], header img[class*="avatar"], [class*="user-info"]');
}

if (typeof window !== 'undefined') {
  window.xhsExtract = {
    searchNotes: xhsExtractSearchNotes,
    noteDetail: xhsExtractNoteDetail,
    user: xhsExtractUser,
    comments: xhsExtractComments,
    isLoggedIn: xhsIsLoggedIn,
  };
}
