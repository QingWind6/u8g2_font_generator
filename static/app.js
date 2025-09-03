(function(){
  const form = document.getElementById('form');
  const btn = document.getElementById('btnGen');
  const logs = document.getElementById('logs');
  const links = document.getElementById('links');

  // 新增进度条元素的引用
  const progressArea = document.getElementById('progress-area');
  const progressLabel = document.getElementById('progress-label');
  const progressBarInner = document.getElementById('progress-bar-inner');
  let pollInterval = null; // 用于存放定时器的ID

  function setBusy(b){
    btn.disabled = b;
    btn.textContent = b ? '生成中…' : '生成字体';
  }

  // 新增：更新进度条的函数
  function updateProgress(step) {
    let percent = 0;
    let label = '准备中...';
    switch(step) {
      case 'init':
        percent = 10;
        label = '任务已初始化...';
        break;
      case 'otf2bdf':
        percent = 40;
        label = '步骤 1/2: 转换 TTF/OTF 为 BDF 格式...';
        break;
      case 'bdfconv':
        percent = 75;
        label = '步骤 2/2: 转换 BDF 为 U8g2 头文件...';
        break;
      case 'done':
        percent = 100;
        label = '处理完成！';
        break;
    }
    progressLabel.textContent = label;
    progressBarInner.style.width = percent + '%';
  }

  // 新增：轮询函数
  function pollStatus(taskId) {
    pollInterval = setInterval(async () => {
      try {
        const resp = await fetch(`/api/status/${taskId}`);
        if (!resp.ok) { // 如果查询本身就失败了（比如404）
          throw new Error(`状态查询失败: ${resp.statusText}`);
        }
        const data = await resp.json();
        
        if (data.ok && data.task) {
          const task = data.task;
          logs.textContent = (task.log || []).join('\n');
          updateProgress(task.step);
          
          // 检查任务是否完成或失败
          if (task.status === 'complete') {
            clearInterval(pollInterval); // 停止轮询
            setBusy(false);
            const result = task.result;
            
            // 自动下载
            const a = document.createElement('a');
            a.href = result.files.header;
            a.download = result.header_name || 'u8g2_font.h';
            document.body.appendChild(a);
            a.click();
            a.remove();
            
            // 显示下载链接
            links.innerHTML = `
              <a href="${result.files.header}" download>重新下载头文件</a>
              <a href="${result.files.bdf}" download>下载 BDF</a>
              <a href="${result.files.log}" download>下载详细日志</a>
            `;
          } else if (task.status === 'failed') {
            clearInterval(pollInterval); // 停止轮询
            setBusy(false);
            logs.textContent = `生成失败：\n${task.error || '未知错误'}`;
            links.innerHTML = '';
          }
        }
      } catch (err) {
        clearInterval(pollInterval); // 出错时也停止轮询
        setBusy(false);
        logs.textContent = '轮询任务状态时发生错误: ' + (err.message || err);
      }
    }, 1500); // 每 1.5 秒查询一次
  }

  // 重写整个 submit 事件监听
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    // 清理旧状态
    if (pollInterval) clearInterval(pollInterval);
    logs.textContent = '开始提交任务…';
    links.innerHTML = '';
    setBusy(true);
    progressArea.style.display = 'block';
    updateProgress('init');

    try {
      const fd = new FormData(form);
      const resp = await fetch('/api/generate', {
        method: 'POST',
        body: fd
      });

      const data = await resp.json().catch(() => null);
      if (!resp.ok || !data || !data.ok) {
        // 这是提交任务阶段的失败
        logs.textContent = '任务提交失败：' + ((data && data.log ? data.log.join('\n') : null) || resp.statusText);
        setBusy(false);
        progressArea.style.display = 'none';
        return;
      }
      
      // 任务提交成功，后端返回了 task_id
      logs.textContent = '任务已成功提交，开始处理...';
      pollStatus(data.task_id); // 开始轮询

    } catch (err) {
      logs.textContent = '请求失败：' + (err && err.message ? err.message : err);
      setBusy(false);
      progressArea.style.display = 'none';
    }
  });
})();