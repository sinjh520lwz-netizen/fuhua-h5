const API_BASE = '/api';

const api = {
  async get(url) {
    const res = await fetch(API_BASE + url);
    if (!res.ok) throw new Error(`API错误: ${res.status}`);
    return res.json();
  },
  async post(url, data) {
    const res = await fetch(API_BASE + url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    return res.json();
  },
  async put(url, data) {
    const res = await fetch(API_BASE + url, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    return res.json();
  },
  async getShips() { return this.get('/ships'); },
  async getPositions() { return this.get('/positions'); },
  async getAnnouncements() { return this.get('/notices'); },
  async getAnnouncement(id) { return this.get('/notices/' + id); },
  async publishAnnouncement(data) { return this.post('/notices/publish', data); },
  async markRead(id) { return this.post('/notices/' + id + '/read'); },
  async getCrew() { return this.get('/crew'); },
  async getCrewGrouped() { return this.get('/crew/grouped'); }
};
