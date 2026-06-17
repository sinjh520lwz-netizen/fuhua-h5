// API配置 - 修改这里即可切换后端服务器
// 当前指向阿里云ECS，迁移后改为新服务器地址
var API_CONFIG = {
    // 后端API地址，末尾不要加斜杠
    // 示例: 'http://8.138.21.141' 或 'https://api.example.com'
    baseUrl: '',
    
    // 获取完整API地址
    getUrl: function(path) {
        if (this.baseUrl) {
            return this.baseUrl + path;
        }
        return path; // 同源部署时使用相对路径
    }
};
