(function(){
  const form = document.getElementById('form');
  const btn = document.getElementById('btnGen');
  const logs = document.getElementById('logs');
  const links = document.getElementById('links');

  function setBusy(b){
    btn.disabled = b;
    btn.textContent = b ? '生成中…' : '生成字体';
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    logs.textContent = '开始生成…';
    links.innerHTML = '';
    setBusy(true);

    try {
      const fd = new FormData(form);
      // checkbox: include_space 未勾选时不传，勾选时设为 "1"
      if (!fd.get('include_space')) {
        // do nothing
      }

      const resp = await fetch('/api/generate', {
        method: 'POST',
        body: fd
      });

      const data = await resp.json().catch(() => null);
      if (!data) {
        logs.textContent = '服务返回异常';
        setBusy(false);
        return;
      }

      logs.textContent = (data.log || []).join('\n');

      if (data.ok) {
        // 只显示成功提示（后端已精简 log）
        logs.textContent = (data.log || ['生成成功']).join('\n');

        // 自动下载生成的 .h
        try {
          const a = document.createElement('a');
          a.href = data.files.header;
          a.download = data.header_name || 'u8g2_font.h';
          document.body.appendChild(a);
          a.click();
          a.remove();
        } catch (e) {
          // 若浏览器阻止自动下载，仍保留手动链接
        }

        // 提供手动下载链接（简洁）
        links.innerHTML = `
          <a href="${data.files.header}" download>重新下载头文件</a>
          <a href="${data.files.bdf}" download>下载 BDF</a>
          <a href="${data.files.log}" download>下载详细日志</a>
        `;
      } else {
        // 失败只显示简要错误（后端已精简 log）
        logs.textContent = (data.log || ['生成失败']).join('\n');
        links.innerHTML = '';
      }
    } catch (err) {
      logs.textContent = '请求失败：' + (err && err.message ? err.message : err);
    } finally {
      setBusy(false);
    }
  });
})();

