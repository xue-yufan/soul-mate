import { createApp } from "vue";
import App from "./App.vue";
import "./styles.css";
// 代码高亮配色：走 highlight.js 官方主题，比自己手写十几个 token 类全得多
import "highlight.js/styles/github-dark-dimmed.css";

createApp(App).mount("#app");
