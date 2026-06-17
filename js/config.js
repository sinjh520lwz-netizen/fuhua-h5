// API配置 - 修改这里即可切换后端服务器
var API_CONFIG = {
    // 后端API地址，末尾不要加斜杠
    baseUrl: 'http://8.138.21.141',
    
    // 获取完整API地址
    getUrl: function(path) {
        if (this.baseUrl) {
            return this.baseUrl + path;
        }
        return path;
    }
};