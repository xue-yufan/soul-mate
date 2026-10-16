/**
 * spike 的 preload
 *
 * 结构和主应用一样：contextIsolation 开着、只暴露几个具名方法，
 * 顺便验证「透明无边框窗口 + 预加载脚本 + IPC」这条链路没问题。
 */

const { contextBridge, ipcRenderer } = require("electron");

/** 把一条主进程 → 渲染层的通知包成「订阅 + 退订」 */
function subscribe(channel, callback) {
    const listener = () => callback();
    ipcRenderer.on(channel, listener);
    return () => ipcRenderer.removeListener(channel, listener);
}

contextBridge.exposeInMainWorld("spike", {
    info: () => ipcRenderer.invoke("spike:info"),
    setClickThrough: (on) => ipcRenderer.invoke("spike:ignore-mouse", on),
    setAlwaysOnTop: (on) => ipcRenderer.invoke("spike:always-on-top", on),
    onState: (callback) => {
        const listener = (_event, state) => callback(state);
        ipcRenderer.on("spike:state", listener);
        return () => ipcRenderer.removeListener("spike:state", listener);
    },
    onToggleGlass: (callback) => subscribe("spike:toggle-glass", callback),
    onToggleWebglAlpha: (callback) =>
        subscribe("spike:toggle-webgl-alpha", callback),
});
