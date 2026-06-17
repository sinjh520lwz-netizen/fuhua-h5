let allShips = [];
let allPositions = {};
let shipsWithPos = [];
let currentTab = 'map';
let isAdmin = new URLSearchParams(location.search).has('admin');
let mapView = 'map';
let crewData = {};

// ========== 初始化 ==========
document.addEventListener('DOMContentLoaded', async () => {
  if (isAdmin) document.getElementById('addAnnounceBtn').style.display = '';
  initMap();
  await loadData();
  switchTab('map');
});

async function loadData() {
  try {
    const [shipsRes, posRes] = await Promise.all([api.getShips(), api.getPositions()]);
    allShips = shipsRes.data || shipsRes || [];
    const positions = posRes.data || posRes || [];
    
    allPositions = {};
    (Array.isArray(positions) ? positions : []).forEach(p => {
      allPositions[p.shipid || p.beidouId] = p;
    });

    shipsWithPos = allShips.map(ship => ({
      ship,
      position: allPositions[ship.beidouId] || null
    }));

    Util.cache('shipsWithPos', shipsWithPos);
    Util.cache('lastUpdate', Date.now());
  } catch(e) {
    console.error('加载数据失败:', e);
    const cached = Util.cache('shipsWithPos');
    if (cached) { shipsWithPos = cached.data; allShips = shipsWithPos.map(s => s.ship); }
  }
}

// ========== Tab切换 ==========
function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById('page-' + tab).classList.add('active');
  document.querySelectorAll('.tab-item').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === tab);
  });
  if (tab === 'map') { renderMap(); }
  else if (tab === 'announce') { loadAnnouncements(); }
  else if (tab === 'crew') { loadCrew(); }
  else if (tab === 'archive') { loadArchive(); }
}

// ========== 船位 ==========
function renderMap() {
  if (mapView === 'map') {
    renderMapMarkers(shipsWithPos);
  } else {
    renderShipList();
  }
}

function switchMapView(mode) {
  mapView = mode;
  document.querySelectorAll('.top-tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('mapContainer').style.display = mode === 'map' ? 'block' : 'none';
  document.getElementById('shipList').style.display = mode === 'list' ? 'block' : 'none';
  if (mode === 'list') renderShipList();
  else renderMapMarkers(shipsWithPos);
}

function renderShipList() {
  const groups = { sailing: [], moored: [], offline: [] };
  shipsWithPos.forEach(item => {
    const s = Util.getShipStatus(item.position);
    groups[s.status].push(item);
  });

  const statsHtml = `<span>🟢 航行 ${groups.sailing.length}</span><span>🔵 停泊 ${groups.moored.length}</span><span>⚫ 离线 ${groups.offline.length}</span>`;
  document.getElementById('statsBar').innerHTML = statsHtml;

  let html = '';
  const renderGroup = (title, emoji, items, statusClass) => {
    if (!items.length) return '';
    let h = `<div class="list-section"><div class="section-title">${emoji} ${title}</div>`;
    items.forEach(({ ship, position: pos }) => {
      const s = Util.getShipStatus(pos);
      const warn = Util.hasBanWarning(ship, pos);
      const nameClass = warn ? 'warn' : ship.isSouthSea ? 'southsea' : s.status === 'offline' ? 'dim' : '';
      h += `<div class="ship-item" onclick="showShipDetail(shipsWithPos.find(x=>x.ship.shipId===${ship.shipId}))">
        <div><div class="ship-name ${nameClass}">${ship.shipName}${warn ? ' ⚠️休渔' : ''}${ship.isSouthSea ? ' 🌊南沙' : ''}</div>
        <div class="ship-meta">${s.status === 'sailing' ? (pos.speed + '节 ' + Util.azimuthToDir(pos.azimuth)) : Util.formatTime(pos?.postime)}</div></div>
        <div style="text-align:right"><span class="ship-status ${statusClass}">${s.text}</span><div class="ship-owner">${ship.owner}</div></div></div>`;
    });
    return h + '</div>';
  };

  html += renderGroup('航行中', '🚢', groups.sailing, 'sailing');
  html += renderGroup('停泊中', '⚓', groups.moored, 'moored');
  html += renderGroup('离线', '⚫', groups.offline, 'offline');
  document.getElementById('listContent').innerHTML = html;
}

async function refreshPositions() {
  const btn = document.getElementById('refreshBtn');
  btn.textContent = '⏳';
  await loadData();
  renderMap();
  btn.textContent = '🔄';
}

function showShipDetail(item) {
  if (!item) return;
  const { ship, position: pos } = item;
  const s = Util.getShipStatus(pos);
  const warn = Util.hasBanWarning(ship, pos);

  let html = `<div class="card-title">${ship.shipName} <span class="ship-status ${s.status}">${s.text}</span>
    ${ship.isSouthSea ? '<span class="southsea-tag">南沙作业</span>' : ''}${warn ? '<span class="warn-tag">休渔期航行!</span>' : ''}</div>
    <div class="card-info">
      <div class="info-row"><span class="info-label">船主</span><span>${ship.owner} ${ship.tel}</span></div>
      <div class="info-row"><span class="info-label">航速</span><span>${pos ? pos.speed + ' 节' : '-'}</span></div>
      <div class="info-row"><span class="info-label">航向</span><span>${pos ? Util.azimuthToDir(pos.azimuth) + ' (' + pos.azimuth + '°)' : '-'}</span></div>
      <div class="info-row"><span class="info-label">定位时间</span><span>${Util.formatTime(pos?.postime)}</span></div>
    </div>
    <div class="card-actions">
      <div class="btn-primary" onclick="window.location.href='tel:${ship.tel}'">📞 拨号船主</div>
      <div class="btn-outline" onclick="showArchiveModal(${ship.shipId})">📋 查看档案</div>
    </div>`;

  const card = document.getElementById('detailCard');
  card.innerHTML = html;
  card.style.display = 'block';
  card.onclick = (e) => { if (e.target === card) card.style.display = 'none'; };
}

// ========== 公告 ==========
async function loadAnnouncements() {
  try {
    const res = await api.getAnnouncements();
    const list = res.data || res || [];
    renderAnnouncements(list);
  } catch(e) {
    document.getElementById('announceList').innerHTML = '<div style="padding:40px;text-align:center;color:#999">加载失败</div>';
  }
}

function renderAnnouncements(list) {
  if (!list.length) {
    document.getElementById('announceList').innerHTML = '<div style="padding:40px;text-align:center;color:#999">暂无公告</div>';
    return;
  }
  let html = '';
  list.forEach(a => {
    html += `<div class="announce-item" onclick="showAnnounceDetail(${a.id})">
      <div class="announce-title">
        ${!a.hasRead ? '<span class="unread-dot"></span>' : ''}
        ${a.pinned ? '<span class="pinned-tag">置顶</span>' : ''}
        ${a.urgent ? '<span class="urgent-tag">紧急</span>' : ''}
        ${a.title}
      </div>
      <span class="announce-type ${a.type}">${a.type}</span>
      <div class="announce-content">${a.content}</div>
      <div class="announce-time">${a.publishTime || ''}</div>
    </div>`;
  });
  document.getElementById('announceList').innerHTML = html;
}

async function showAnnounceDetail(id) {
  try {
    const res = await api.getAnnouncement(id);
    const a = res.data || res;
    const html = `<div class="modal-close" onclick="closeModal(event)">✕</div>
      <h3 style="margin-bottom:10px">${a.title} <span class="announce-type ${a.type}">${a.type}</span></h3>
      <div style="font-size:13px;color:#999;margin-bottom:12px">${a.publishTime || ''} | ${a.publisher || ''}</div>
      <div style="line-height:1.8;font-size:15px">${a.content}</div>
      <div style="margin-top:16px"><button class="btn-primary" onclick="api.markRead(${a.id});loadAnnouncements();closeModal(event);">确认已读</button></div>`;
    document.getElementById('modalContent').innerHTML = html;
    document.getElementById('modal').style.display = 'flex';
  } catch(e) {}
}

function showAnnounceForm() {
  const form = document.getElementById('announceForm');
  form.style.display = form.style.display === 'none' ? 'block' : 'none';
  if (form.style.display === 'block') {
    form.innerHTML = `<input id="aTitle" placeholder="公告标题">
      <select id="aType"><option>通知</option><option>休渔</option><option>年检</option><option>台风</option></select>
      <textarea id="aContent" placeholder="公告内容"></textarea>
      <div style="display:flex;gap:10px"><label><input type="checkbox" id="aUrgent"> 紧急</label><label><input type="checkbox" id="aPinned"> 置顶</label></div>
      <button class="btn-primary" onclick="publishAnnounce()" style="margin-top:10px">发布公告</button>`;
  }
}

async function publishAnnounce() {
  const data = {
    title: document.getElementById('aTitle').value,
    type: document.getElementById('aType').value,
    content: document.getElementById('aContent').value,
    urgent: document.getElementById('aUrgent').checked,
    pinned: document.getElementById('aPinned').checked
  };
  if (!data.title || !data.content) return alert('请填写标题和内容');
  await api.publishAnnouncement(data);
  document.getElementById('announceForm').style.display = 'none';
  loadAnnouncements();
}

// ========== 船员 ==========
async function loadCrew() {
  try {
    const res = await api.getCrewGrouped();
    crewData = res.data || res || {};
    renderCrew(crewData);
  } catch(e) {
    document.getElementById('crewList').innerHTML = '<div style="padding:40px;text-align:center;color:#999">加载失败</div>';
  }
}

function renderCrew(data) {
  const groups = Array.isArray(data) ? data : Object.entries(data).map(([k, v]) => ({ shipName: k, members: v }));
  if (!groups.length) {
    document.getElementById('crewList').innerHTML = '<div style="padding:40px;text-align:center;color:#999">暂无船员数据</div>';
    return;
  }
  let html = '';
  groups.forEach((g, i) => {
    html += `<div class="crew-group">
      <div class="crew-group-header" onclick="toggleCrewGroup(${i})">
        <span>🚢 ${g.shipName} (${g.members ? g.members.length : 0}人)</span>
        <span class="arrow" id="arrow${i}">▶</span>
      </div>
      <div class="crew-members" id="crewGroup${i}">`;
    (g.members || []).forEach(m => {
      html += `<div class="crew-member" onclick="showCrewDetail(${JSON.stringify(m).replace(/"/g, '&quot;')})">
        <div><div class="crew-name">${m.name}</div><div class="crew-phone">${m.phone || ''}</div></div>
        <span class="crew-position">${m.position}</span></div>`;
    });
    html += '</div></div>';
  });
  document.getElementById('crewList').innerHTML = html;
}

function toggleCrewGroup(i) {
  const el = document.getElementById('crewGroup' + i);
  const arrow = document.getElementById('arrow' + i);
  el.classList.toggle('open');
  arrow.classList.toggle('open');
}

function searchCrew() {
  const kw = document.getElementById('crewSearch').value.trim().toLowerCase();
  const groups = Array.isArray(crewData) ? crewData : Object.entries(crewData).map(([k, v]) => ({ shipName: k, members: v }));
  if (!kw) { renderCrew(groups); return; }
  const filtered = groups.map(g => ({
    shipName: g.shipName,
    members: (g.members || []).filter(m => m.name.toLowerCase().includes(kw) || g.shipName.toLowerCase().includes(kw))
  })).filter(g => g.members.length > 0);
  renderCrew(filtered);
}

function showCrewDetail(m) {
  const html = `<div class="modal-close" onclick="closeModal(event)">✕</div>
    <h3 style="margin-bottom:12px">${m.name} <span class="crew-position">${m.position}</span></h3>
    <div class="detail-field"><span class="detail-label">电话</span><a href="tel:${m.phone}" style="color:var(--primary)">${m.phone || '-'}</a></div>
    <div class="detail-field"><span class="detail-label">身份证</span><span class="detail-value">${Util.maskIdCard(m.idCard)}</span></div>
    <div class="detail-field"><span class="detail-label">证件到期</span><span class="detail-value">${m.certExpire || '-'}</span></div>`;
  document.getElementById('modalContent').innerHTML = html;
  document.getElementById('modal').style.display = 'flex';
}

// ========== 档案 ==========
function loadArchive() {
  if (!allShips.length) return;
  let html = '';
  allShips.forEach(ship => {
    const as = Util.annualStatus(ship.annualInspect);
    html += `<div class="archive-item" onclick="showArchiveModal(${ship.shipId})">
      <div><div class="archive-name">${ship.shipName}</div>
      <div class="archive-info">${ship.tonnage}吨 / ${ship.power}kW / ${ship.owner}</div></div>
      <span class="annual-status" title="${as.text}">${as.icon}</span></div>`;
  });
  document.getElementById('archiveList').innerHTML = html;
}

function showArchiveModal(shipId) {
  const item = shipsWithPos.find(x => x.ship.shipId === shipId);
  if (!item) return;
  const ship = item.ship;
  const as = Util.annualStatus(ship.annualInspect);
  const fields = [
    ['船号', ship.shipName], ['总吨位', ship.tonnage + '吨'], ['功率', ship.power + 'kW'],
    ['船长', ship.length + 'm'], ['型宽', ship.width + 'm'], ['型深', ship.depth + 'm'],
    ['净吨位', ship.netTonnage + '吨'], ['船体材料', ship.hullMaterial], ['船籍港', ship.port],
    ['作业类型', ship.fishingType], ['持证人', ship.owner], ['电话', ship.tel],
    ['地址', ship.address], ['AIS码', ship.aisCode], ['北斗ID', ship.beidouId],
    ['建造日期', ship.buildDate], ['年检时间', ship.annualInspect || '未录入'],
    ['年检状态', `${as.icon} ${as.text}`], ['备注', ship.remark || '-']
  ];
  let html = `<div class="modal-close" onclick="closeModal(event)">✕</div>
    <h3 style="margin-bottom:12px">${ship.shipName} ${ship.isSouthSea ? '<span class="southsea-tag">南沙作业</span>' : ''}</h3>`;
  fields.forEach(([label, value]) => {
    html += `<div class="detail-field"><span class="detail-label">${label}</span><span class="detail-value">${value}</span></div>`;
  });
  html += `<div style="margin-top:16px"><button class="btn-primary" onclick="window.location.href='tel:${ship.tel}'">📞 拨号船主</button></div>`;
  document.getElementById('modalContent').innerHTML = html;
  document.getElementById('modal').style.display = 'flex';
}

function closeModal(e) {
  if (e.target.classList.contains('modal') || e.target.classList.contains('modal-close')) {
    document.getElementById('modal').style.display = 'none';
  }
}
