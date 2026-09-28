"""Real browser checks against a running local server (no WorkBuddy writes)."""
import argparse
import json
import os
from pathlib import Path
import time

from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]


def main(url):
    task_id='ui-smoke-'+str(int(time.time()))
    errors=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':1512,'height':982},device_scale_factor=1)
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(url)
        token=os.environ.get('AUTOMATION_API_TOKEN')
        if token:
            page.locator('#login-dialog input').fill(token)
            page.get_by_role('button',name='登录',exact=True).click()
        expect(page.get_by_role('heading',name='让日常任务，按时完成')).to_be_visible()
        # New task / weekly schedule / edit / enable / manual run.
        page.get_by_role('button',name='新建任务').click()
        form=page.locator('#task-form')
        form.locator('[name="id"]').fill(task_id)
        form.locator('[name="name"]').fill('UI 验证任务')
        form.locator('[name="kind"]').select_option('weekly')
        form.locator('[name="weekday"][value="2"]').check()
        form.locator('[name="enabled"]').check()
        form.get_by_role('button',name='保存任务').click()
        page.wait_for_timeout(300)
        diagnostics=page.locator('#toasts').inner_text()
        try:
            expect(page.locator('#task-dialog')).not_to_be_visible()
        except AssertionError:
            print('UI diagnostics:',diagnostics,errors,
                form.evaluate('(f) => Array.from(f.elements).filter(x => x.validity && !x.validity.valid).map(x => ({name:x.name,message:x.validationMessage}))'))
            raise
        row=page.locator('tr').filter(has_text='UI 验证任务')
        expect(row).to_be_visible()
        row.get_by_role('button',name='编辑',exact=True).click()
        expect(form.locator('[name="kind"]')).to_have_value('weekly')
        form.locator('[name="description"]').fill('UI smoke test')
        form.get_by_role('button',name='保存任务').click()
        row.get_by_role('button',name='运行',exact=True).click()
        expect(row.locator('.badge')).to_have_text('成功',timeout=15000)
        row.locator('[data-select]').check()
        page.get_by_role('button',name='关闭定时',exact=True).click()
        expect(row.get_by_role('switch')).to_have_attribute('aria-checked','false')
        page.get_by_role('button',name='开启定时',exact=True).click()
        expect(row.get_by_role('switch')).to_have_attribute('aria-checked','true')
        page.get_by_role('button',name='运行所选',exact=True).click()
        # Log modal.
        row.get_by_role('button',name='记录',exact=True).click()
        expect(page.get_by_role('heading',name='执行记录',exact=True)).to_be_visible()
        page.get_by_role('button',name='查看日志').first.click()
        expect(page.locator('#log-dialog')).to_be_visible()
        expect(page.locator('#log-output')).to_contain_text('示例任务',timeout=15000)
        page.locator('#log-dialog').get_by_role('button',name='关闭',exact=True).last.click()
        # Settings persist, preserving unfinished drafts during polling.
        page.get_by_role('link',name='全局配置').click()
        settings=page.locator('#settings-form')
        previous=settings.locator('[name="max_parallel"]').input_value()
        settings.locator('[name="max_parallel"]').fill('3')
        page.locator('.section-title h2').click()
        page.wait_for_timeout(3300)
        expect(settings.locator('[name="max_parallel"]')).to_have_value('3')
        settings.get_by_role('button',name='保存全局配置').click()
        settings.locator('[name="max_parallel"]').fill(previous)
        settings.get_by_role('button',name='保存全局配置').click()
        # Write-only environment values and doctor.
        page.get_by_role('link',name='运行环境').click()
        expect(page.get_by_text('venv 已启用',exact=True)).to_be_visible()
        page.get_by_role('button',name='检查 WorkBuddy').click()
        page.locator('#env-new-name').fill('UI_SMOKE_TOKEN')
        page.locator('#env-new-value').fill('smoke-private-secret')
        page.get_by_role('button',name='保存环境变量').click()
        expect(page.locator('[data-env="UI_SMOKE_TOKEN"]')).to_have_value('')
        assert 'smoke-private-secret' not in page.content()
        page.on('dialog',lambda dialog:dialog.accept())
        page.locator('[data-action="env-remove"][data-id="UI_SMOKE_TOKEN"]').click()
        expect(page.locator('code').filter(has_text='UI_SMOKE_TOKEN')).to_have_count(0)
        # Cleanup, then dashboard screenshot at desktop and mobile widths.
        page.locator('nav [data-page="tasks"]').click()
        row=page.locator('tr').filter(has_text='UI 验证任务')
        expect(row.locator('.badge')).to_have_text('成功',timeout=15000)
        row.get_by_role('button',name='删除 UI 验证任务').click()
        expect(row).to_have_count(0)
        artifacts=ROOT/'data/artifacts'
        artifacts.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(artifacts/'dashboard.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        expect(page.get_by_role('button',name='新建任务')).to_be_visible()
        page.screenshot(path=str(artifacts/'dashboard-mobile.png'),full_page=True)
        browser.close()
    if errors:
        raise RuntimeError('\n'.join(errors))
    print(json.dumps({'status':'passed','checks':['task CRUD','weekly schedule','single and batch run','batch toggles','logs','settings persistence','draft preservation','write-only environment','WorkBuddy doctor','responsive layout'],'screenshots':str(ROOT/'data/artifacts')},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8765')
    main(parser.parse_args().url)
