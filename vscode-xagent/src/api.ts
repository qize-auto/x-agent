/**
 * X-Agent HTTP API 客户端
 */
export class XAgentAPI {
  private baseUrl: string;

  constructor(host: string, port: number) {
    this.baseUrl = `http://${host}:${port}`;
  }

  async health(): Promise<boolean> {
    try {
      const res = await fetch(`${this.baseUrl}/health`);
      const data = await res.json() as { ok: boolean };
      return data.ok === true;
    } catch {
      return false;
    }
  }

  async chat(message: string): Promise<{ response: string }> {
    const res = await fetch(`${this.baseUrl}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    if (!res.ok) {
      const err = await res.json() as { error?: string };
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    return res.json() as Promise<{ response: string }>;
  }

  async task(goal: string): Promise<{ goal: string; status: string; subtasks: any[] }> {
    const res = await fetch(`${this.baseUrl}/task`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ goal }),
    });
    if (!res.ok) {
      const err = await res.json() as { error?: string };
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    return res.json() as Promise<{ goal: string; status: string; subtasks: any[] }>;
  }

  async status(): Promise<any> {
    const res = await fetch(`${this.baseUrl}/status`);
    return res.json();
  }

  async tools(): Promise<{ tools: any[] }> {
    const res = await fetch(`${this.baseUrl}/tools`);
    return res.json() as Promise<{ tools: any[] }>;
  }
}
