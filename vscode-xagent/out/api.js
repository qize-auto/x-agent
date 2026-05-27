"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.XAgentAPI = void 0;
/**
 * X-Agent HTTP API 客户端
 */
class XAgentAPI {
    constructor(host, port) {
        this.baseUrl = `http://${host}:${port}`;
    }
    async health() {
        try {
            const res = await fetch(`${this.baseUrl}/health`);
            const data = await res.json();
            return data.ok === true;
        }
        catch {
            return false;
        }
    }
    async chat(message) {
        const res = await fetch(`${this.baseUrl}/chat`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message }),
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error || `HTTP ${res.status}`);
        }
        return res.json();
    }
    async task(goal) {
        const res = await fetch(`${this.baseUrl}/task`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ goal }),
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error || `HTTP ${res.status}`);
        }
        return res.json();
    }
    async status() {
        const res = await fetch(`${this.baseUrl}/status`);
        return res.json();
    }
    async tools() {
        const res = await fetch(`${this.baseUrl}/tools`);
        return res.json();
    }
}
exports.XAgentAPI = XAgentAPI;
//# sourceMappingURL=api.js.map