// API配置 - 静态数据模式（GitHub Pages）
var API_CONFIG = {
    baseUrl: '',
    
    // 静态数据映射
    _staticMap: {
        '/api/ships': '/data/ships.json',
        '/api/announcements': '/data/announcements.json'
    },
    
    getUrl: function(path) {
        // 优先使用静态映射
        if (this._staticMap[path]) {
            return this._staticMap[path];
        }
        if (this.baseUrl) {
            return this.baseUrl + path;
        }
        return path;
    }
};