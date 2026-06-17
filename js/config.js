// API配置 - 指向阿里云ECS（HTTPS）
var API_CONFIG = {
    baseUrl: 'https://8.138.21.141',
    
    getUrl: function(path) {
        return this.baseUrl + path;
    }
};