const feedbackUi = { dirty: false, saving: false, ready: false, revision: 0 };
const feedbackModeLabels = { follow: "跟随遥控状态", manual: "人工模式", auto: "自动模式", remote: "遥控模式" };

/** 绑定车辆反馈表单；表单就绪后由现有状态轮询驱动回显。 */
function bindVehicleFeedback() {
  feedbackUi.form = document.getElementById("vehicleFeedbackForm");
  feedbackUi.mode = document.getElementById("feedbackMode");
  feedbackUi.pressure1 = document.getElementById("feedbackPressure1");
  feedbackUi.pressure2 = document.getElementById("feedbackPressure2");
  feedbackUi.save = document.getElementById("saveVehicleFeedback");
  feedbackUi.status = document.getElementById("vehicleFeedbackStatus");
  feedbackUi.effective = document.getElementById("vehicleFeedbackEffective");
  feedbackUi.form.addEventListener("input", markVehicleFeedbackDirty);
  feedbackUi.form.addEventListener("submit", saveVehicleFeedback);
}

/** 标记未保存编辑，阻止周期轮询覆盖输入，并记录保存期间的后续编辑。 */
function markVehicleFeedbackDirty() {
  feedbackUi.dirty = true;
  feedbackUi.revision += 1;
  setVehicleFeedbackMessage("有未保存的修改");
}

/** 设置表单提示及错误样式；通过文本节点避免服务端错误内容成为 HTML。 */
function setVehicleFeedbackMessage(message, error = false) {
  feedbackUi.status.textContent = message;
  feedbackUi.status.classList.toggle("feedback-error", error);
}

/** 使用服务端默认值更新表单，单独显示有效 JSON 控制覆盖后的模式和两路气压。 */
function renderVehicleFeedback(config) {
  if (!config || !feedbackUi.form) return;
  feedbackUi.effective.textContent = `当前反馈：${feedbackModeLabels[config.effective_mode] || "未知"}；气压 ${Number(config.effective_air_pressure_1 ?? config.air_pressure_1).toFixed(2)} / ${Number(config.effective_air_pressure_2 ?? config.air_pressure_2).toFixed(2)} MPa`;
  if (!feedbackUi.dirty && !feedbackUi.saving) {
    feedbackUi.mode.value = config.mode;
    feedbackUi.pressure1.value = Number(config.air_pressure_1).toFixed(2);
    feedbackUi.pressure2.value = Number(config.air_pressure_2).toFixed(2);
  }
  if (!feedbackUi.ready) {
    feedbackUi.ready = true;
    feedbackUi.save.disabled = false;
    setVehicleFeedbackMessage("配置已读取，保存后立即应用并在重启后保留");
  }
}

/** 校验并保存模式与气压；保存失败保留编辑，保存成功后保护同时发生的新编辑。 */
async function saveVehicleFeedback(event) {
  event.preventDefault();
  if (feedbackUi.saving || !feedbackUi.form.reportValidity()) return;
  const revision = feedbackUi.revision;
  const payload = {
    mode: feedbackUi.mode.value,
    air_pressure_1: Number(feedbackUi.pressure1.value),
    air_pressure_2: Number(feedbackUi.pressure2.value),
  };
  feedbackUi.saving = true;
  feedbackUi.save.disabled = true;
  setVehicleFeedbackMessage("正在保存…");
  try {
    const response = await fetch("/api/vehicle-feedback", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const saved = await response.json();
    if (!response.ok) throw new Error(saved.error || "保存失败");
    feedbackUi.dirty = revision !== feedbackUi.revision;
    setVehicleFeedbackMessage(feedbackUi.dirty ? "上一版已保存，当前仍有未保存的修改" : "已保存并应用，重启后保留");
    feedbackUi.saving = false;
    renderVehicleFeedback(saved);
  } catch (error) {
    setVehicleFeedbackMessage(error.message || "保存失败，请检查连接后重试", true);
  } finally {
    feedbackUi.saving = false;
    feedbackUi.save.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", bindVehicleFeedback);
