/* X-Agent 前端面板逻辑 */

let bridge = null;
let toolCount = 0;

// 初始化 QWebChannel
new QWebChannel(qt.webChannelTransport, function(channel) {
  bridge = channel.objects.agentBridge;
  if (bridge) {
    // 加载配置
    bridge.getConfig(function(resp) {
      try {
        const cfg = JSON.parse(resp);
        if (cfg.model_id) {
          document.querySelector('header h1').textContent = '🤖 X-Agent — ' + cfg.model_id;
        }
      } catch (e) {}
    });
  }
});

// DOM
const chatHistory = document.getElementById('chat-history');
const inputBox = document.getElementById('input-box');
const sendBtn = document.getElementById('send-btn');
const statusBadge = document.getElementById('status-badge');
const toolList = document.getElementById('tool-list');
const toastEl = document.getElementById('toast');
const swarmPanel = document.getElementById('swarm-panel');

// 发送消息
function sendMessage() {
  const text = inputBox.value.trim();
  if (!text) return;
  appendUserMessage(text);
  inputBox.value = '';
  if (bridge) {
    bridge.sendMessage(text, function(resp) {
      try {
        const r = JSON.parse(resp);
        if (!r.ok) showToast('错误: ' + (r.error || '未知'));
      } catch (e) {}
    });
  } else {
    appendError('AgentBridge 未连接，请重启应用。');
  }
}

sendBtn.addEventListener('click', sendMessage);
inputBox.addEventListener('keydown', function(e) {
  if (e.ctrlKey && e.key === 'Enter') sendMessage();
});

// 👁 截图/感知按钮
document.addEventListener('DOMContentLoaded', function() {
  const perceiveBtn = document.getElementById('perceive-btn');
  if (perceiveBtn) {
    perceiveBtn.addEventListener('click', function() {
      if (bridge) {
        bridge.perceiveScreen(function(resp) {
          try {
            const r = JSON.parse(resp);
            if (r.ok) {
              appendVisionContext(r.context);
              showToast('👁 已捕获屏幕状态');
            } else {
              showToast('感知失败: ' + (r.error || '未知'));
            }
          } catch (e) {}
        });
      }
    });
  }

  // 🐝 Swarm 面板切换
  const swarmToggleBtn = document.getElementById('swarm-toggle-btn');
  if (swarmToggleBtn) {
    swarmToggleBtn.addEventListener('click', function() {
      if (swarmPanel) {
        const showing = swarmPanel.style.display === 'block';
        swarmPanel.style.display = showing ? 'none' : 'block';
        if (!showing) refreshSwarmStatus();
      }
    });
  }

  // 定期刷新 Swarm 状态
  setInterval(function() {
    if (swarmPanel && swarmPanel.style.display === 'block') {
      refreshSwarmStatus();
    }
  }, 5000);
});

// 🐝 Swarm 状态刷新
function refreshSwarmStatus() {
  if (!bridge) return;
  bridge.getSwarmStatus(function(resp) {
    try {
      const r = JSON.parse(resp);
      if (!r.ok) return;
      document.getElementById('swarm-enabled').textContent = r.enabled ? '启用' : '关闭';
      document.getElementById('swarm-workers').textContent = r.workers;
      if (r.stats) {
        document.getElementById('swarm-pending').textContent = r.stats.pending;
        document.getElementById('swarm-running').textContent = r.stats.running;
        document.getElementById('swarm-completed').textContent = r.stats.completed;
        document.getElementById('swarm-failed').textContent = r.stats.failed;
      }
      const recentEl = document.getElementById('swarm-recent');
      if (recentEl && r.recent) {
        let html = '<table style="width:100%;border-collapse:collapse;"><tr><th>节点</th><th>状态</th><th>重试</th></tr>';
        r.recent.forEach(function(cp) {
          const color = cp.status === 'completed' ? 'green' : (cp.status === 'failed' ? 'red' : 'orange');
          html += '<tr><td>' + escapeHtml(cp.node_id) + '</td><td style="color:' + color + '">' + cp.status + '</td><td>' + cp.retry_count + '</td></tr>';
        });
        html += '</table>';
        recentEl.innerHTML = html;
      }
    } catch (e) {}
  });
}

// 消息渲染
function appendUserMessage(text) {
  const div = document.createElement('div');
  div.className = 'msg user';
  div.innerHTML = '<div class="label">You</div>' + escapeHtml(text);
  chatHistory.appendChild(div);
  scrollToBottom();
}

function appendAgentMessage(text) {
  const div = document.createElement('div');
  div.className = 'msg agent';
  div.innerHTML = '<div class="label">Agent</div>' + escapeHtml(text);
  chatHistory.appendChild(div);
  scrollToBottom();
}

function appendError(text) {
  const div = document.createElement('div');
  div.className = 'msg error';
  div.innerHTML = '<div class="label">Error</div>' + escapeHtml(text);
  chatHistory.appendChild(div);
  scrollToBottom();
}

function appendVisionContext(text) {
  const div = document.createElement('div');
  div.className = 'msg vision';
  div.innerHTML = '<div class="label">👁 视觉感知</div><pre style="white-space:pre-wrap;font-size:11px;max-height:200px;overflow:auto;background:rgba(0,0,0,0.03);padding:8px;border-radius:6px;">' + escapeHtml(text) + '</pre>';
  chatHistory.appendChild(div);
  scrollToBottom();
}

function appendToolCall(name) {
  toolCount++;
  const li = document.createElement('li');
  li.innerHTML = '<span class="name">' + escapeHtml(name) + '</span><span class="time">' + new Date().toLocaleTimeString() + '</span>';
  toolList.insertBefore(li, toolList.firstChild);
  if (toolList.children.length > 20) toolList.removeChild(toolList.lastChild);
}

function setStatus(state, label) {
  statusBadge.className = 'badge ' + state;
  statusBadge.textContent = label;
}

function showToast(msg) {
  toastEl.textContent = msg;
  toastEl.classList.add('show');
  setTimeout(() => toastEl.classList.remove('show'), 3000);
}

function showPlanConfirm(planData) {
  const div = document.createElement('div');
  div.className = 'msg agent';
  div.id = 'plan-confirm-' + Date.now();

  let html = '<div class="label">任务计划</div>';
  html += '<div style="margin:8px 0;font-size:12px;color:var(--text-muted)">' + escapeHtml(planData.goal) + '</div>';
  html += '<ol style="padding-left:16px;margin:8px 0;font-size:12px;">';
  planData.subtasks.forEach(function(st, idx) {
    html += '<li style="margin:4px 0;">' + escapeHtml(st.description);
    if (st.tool_hint) html += ' <span style="color:var(--accent)">[' + escapeHtml(st.tool_hint) + ']</span>';
    html += '</li>';
  });
  html += '</ol>';
  html += '<div style="display:flex;gap:8px;margin-top:10px;">';
  html += '<button onclick="confirmPlan(this)" style="padding:4px 12px;border:none;border-radius:6px;background:var(--accent);color:#fff;cursor:pointer;font-size:12px;">确认执行</button>';
  html += '<button onclick="cancelPlan(this)" style="padding:4px 12px;border:none;border-radius:6px;background:var(--border);color:var(--text);cursor:pointer;font-size:12px;">取消</button>';
  html += '</div>';

  div.innerHTML = html;
  div.dataset.plan = JSON.stringify(planData);
  chatHistory.appendChild(div);
  scrollToBottom();
}

function confirmPlan(btn) {
  const div = btn.closest('.msg');
  const planData = div.dataset.plan;
  div.querySelector('button').disabled = true;
  div.querySelector('button').textContent = '执行中…';
  if (bridge) {
    bridge.executePlan(planData, function(resp) {
      try {
        var r = JSON.parse(resp);
        if (!r.ok) showToast('执行失败: ' + (r.error || '未知'));
      } catch (e) {}
    });
  }
}

function cancelPlan(btn) {
  const div = btn.closest('.msg');
  div.remove();
  showToast('已取消任务');
}

function scrollToBottom() {
  chatHistory.scrollTop = chatHistory.scrollHeight;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}
