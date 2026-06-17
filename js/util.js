const Util = {
  // 判断是否休渔期(5.1-8.16)
  isFishingBan() {
    const now = new Date();
    const m = now.getMonth() + 1;
    const d = now.getDate();
    return (m > 5 && m < 8) || (m === 5 && d >= 1) || (m === 8 && d <= 16);
  },

  // 船舶状态判定
  getShipStatus(pos) {
    if (!pos || !pos.postime) return { status: 'offline', text: '离线' };
    const hours = (Date.now() / 1000 - pos.postime) / 3600;
    if (hours > 72) return { status: 'offline', text: '离线' };
    if (pos.speed > 0) return { status: 'sailing', text: '航行中' };
    return { status: 'moored', text: '停泊中' };
  },

  // 休渔期航行警示
  hasBanWarning(ship, pos) {
    if (ship.isSouthSea) return false;
    if (!this.isFishingBan()) return false;
    return this.getShipStatus(pos).status === 'sailing';
  },

  // 格式化时间
  formatTime(timestamp) {
    if (!timestamp) return '无数据';
    const d = new Date(timestamp * 1000);
    const now = new Date();
    const diff = (now - d) / 1000;
    if (diff < 60) return '刚刚';
    if (diff < 3600) return Math.floor(diff / 60) + '分钟前';
    if (diff < 86400) return Math.floor(diff / 3600) + '小时前';
    return d.getMonth() + 1 + '月' + d.getDate() + '日 ' +
      String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  },

  // 航向转方位
  azimuthToDir(az) {
    if (!az && az !== 0) return '-';
    const dirs = ['北', '东北', '东', '东南', '南', '西南', '西', '西北'];
    return dirs[Math.round(az / 45) % 8];
  },

  // 年检状态
  annualStatus(dateStr) {
    if (!dateStr) return { icon: '❓', color: '#95A5A6', text: '未知' };
    const exp = new Date(dateStr);
    const now = new Date();
    const days = (exp - now) / 86400000;
    if (days < 0) return { icon: '❌', color: '#E74C3C', text: '已过期' };
    if (days < 30) return { icon: '⚠️', color: '#F39C12', text: '即将到期' };
    return { icon: '✅', color: '#27AE60', text: '已年检' };
  },

  // 身份证脱敏
  maskIdCard(id) {
    if (!id || id.length < 10) return id || '';
    return id.substring(0, 6) + '******' + id.substring(id.length - 4);
  },

  // 缓存
  cache(key, data) {
    if (data) {
      try { localStorage.setItem('fh_' + key, JSON.stringify({ data, time: Date.now() })); } catch(e) {}
    } else {
      try {
        const raw = localStorage.getItem('fh_' + key);
        return raw ? JSON.parse(raw) : null;
      } catch(e) { return null; }
    }
  }
};
