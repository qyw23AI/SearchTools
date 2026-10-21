#!/usr/bin/python3
"""Native stack keeps each upstream WebView alive, without iframe restrictions."""
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse
import gi
gi.require_version('Gtk','3.0')
try:
    gi.require_version('WebKit2','4.1')
except ValueError:
    gi.require_version('WebKit2','4.0')
from gi.repository import Gio,GLib,Gtk,WebKit2,Gdk
from searchtools.core import config,ROOT,CONFIG


class Desktop(Gtk.Application):
    def __init__(self):
        super().__init__(application_id='io.github.qyw23AI.SearchTools',flags=Gio.ApplicationFlags.NON_UNIQUE if os.getenv('SEARCHTOOLS_SMOKE') else Gio.ApplicationFlags.FLAGS_NONE)
        self.window=None
        self.cfg=config()
        self.views={}
        self.zoom_file=CONFIG.parent/'desktop-state.json'
        try:self.zoom_levels=json.loads(self.zoom_file.read_text())
        except (OSError,ValueError):self.zoom_levels={}

    def do_activate(self):
        if self.window:
            self.window.present()
            return
        self.window=Gtk.ApplicationWindow(application=self,title='SearchTools · 科研工作区')
        self.window.set_default_size(1440,920)
        self.window.set_resizable(True)
        self.window.set_size_request(800,560)
        self.window.set_icon_from_file(str(ROOT/'assets/searchtools.svg'))
        header=Gtk.HeaderBar(title='SearchTools')
        header.set_show_close_button(True)
        header.set_decoration_layout('menu:minimize,maximize,close')
        self.window.set_titlebar(header)
        self.stack=Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(120)
        switcher=Gtk.StackSwitcher(stack=self.stack)
        header.set_custom_title(switcher)
        reload=Gtk.Button.new_from_icon_name('view-refresh-symbolic',Gtk.IconSize.BUTTON)
        reload.set_tooltip_text('刷新当前页面')
        reload.connect('clicked',lambda b:self.stack.get_visible_child().reload())
        header.pack_end(reload)
        zoom_box=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=0)
        for text,action,tip in [('−','out','缩小 Ctrl −'),('100%','reset','重置 Ctrl 0'),('＋','in','放大 Ctrl +')]:
            button=Gtk.Button(label=text)
            button.set_tooltip_text(tip)
            button.connect('clicked',lambda b,a=action:self.zoom(a))
            zoom_box.pack_start(button,False,False,0)
            if action=='reset':self.zoom_label=button
        header.pack_end(zoom_box)
        self.window.connect('key-press-event',self.key_press)
        self.stack.connect('notify::visible-child',lambda *args:self.update_zoom_label())
        self.window.add(self.stack)
        context=WebKit2.WebContext.get_default()
        context.set_network_proxy_settings(WebKit2.NetworkProxyMode.NO_PROXY,None)
        context.connect('download-started',self.download)
        urls={'workspace':f'http://127.0.0.1:{self.cfg["port"]}/','deeptutor':self.cfg['deeptutor_url'],'gpt':self.cfg['gpt_url']}
        for key,title in [('workspace','工作区 · PaperQA2'),('deeptutor','DeepTutor'),('gpt','GPT Researcher')]:
            manager=WebKit2.UserContentManager()
            if key=='workspace':
                manager.add_script(WebKit2.UserScript.new('window.__SEARCHTOOLS_DESKTOP__=true;window.__SEARCHTOOLS_ZOOM_NATIVE__=true;',WebKit2.UserContentInjectedFrames.TOP_FRAME,WebKit2.UserScriptInjectionTime.START,None,None))
            view=WebKit2.WebView.new_with_user_content_manager(manager)
            view.connect('decide-policy',self.policy)
            view.connect('load-failed',self.failed)
            view.connect('create',self.popup)
            view.connect('load-changed',self.loaded)
            self.views[key]=view
            view.set_zoom_level(max(.6,min(2,float(self.zoom_levels.get(key,1)))))
            self.stack.add_titled(view,key,title)
            view.load_uri(urls[key])
        self.window.show_all()
        if os.getenv('SEARCHTOOLS_SMOKE'):
            GLib.timeout_add_seconds(20,self.smoke)

    def zoom(self,action):
        view=self.stack.get_visible_child()
        if not view:return
        level=1 if action=='reset' else round(view.get_zoom_level()+(.1 if action=='in' else -.1),2)
        view.set_zoom_level(max(.6,min(2,level)))
        for name,item in self.views.items():
            self.zoom_levels[name]=item.get_zoom_level()
        self.zoom_file.parent.mkdir(parents=True,exist_ok=True)
        self.zoom_file.write_text(json.dumps(self.zoom_levels))
        self.update_zoom_label()

    def update_zoom_label(self):
        view=self.stack.get_visible_child()
        if not view or not hasattr(self,'zoom_label'):return
        level=view.get_zoom_level()
        self.zoom_label.set_label(f'{round(level*100)}%')
        if view is self.views.get('workspace'):
            view.run_javascript(f'window.searchtoolsSetZoom && window.searchtoolsSetZoom({level})',None,None,None)

    def key_press(self,window,event):
        if event.state & Gdk.ModifierType.CONTROL_MASK:
            key=Gdk.keyval_name(event.keyval)
            action={'plus':'in','equal':'in','KP_Add':'in','minus':'out','KP_Subtract':'out','0':'reset','KP_0':'reset'}.get(key)
            if action:
                self.zoom(action)
                return True
        return False

    def policy(self,view,decision,kind):
        if kind!=WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            return False
        uri=decision.get_navigation_action().get_request().get_uri()
        u=urlparse(uri)
        if u.scheme=='searchtools':
            decision.ignore()
            # Only the trusted workspace can invoke local actions.
            if view is self.views.get('workspace'):
                value=u.path.strip('/')
                if u.netloc=='tool' and value in ('deeptutor','gpt'):
                    self.stack.set_visible_child_name(value)
                elif u.netloc=='zoom' and value in ('in','out','reset'):
                    self.zoom(value)
                elif u.netloc=='folder' and len(value)==32 and all(c in '0123456789abcdef' for c in value):
                    target=Path(self.cfg['data'])/'projects'/value/'papers'
                    if target.is_dir():
                        Gio.AppInfo.launch_default_for_uri(target.as_uri(),None)
            return True
        if u.scheme in ('http','https'):
            if u.hostname in ('127.0.0.1','localhost'):
                return False
            Gio.AppInfo.launch_default_for_uri(uri,None)
            decision.ignore()
            return True
        if u.scheme=='mailto':
            Gio.AppInfo.launch_default_for_uri(uri,None)
            decision.ignore()
            return True
        if u.scheme in ('file','javascript'):
            decision.ignore()
            return True
        return False

    def popup(self,view,action):
        uri=action.get_request().get_uri()
        if uri and uri.startswith(('https://','http://')):
            Gio.AppInfo.launch_default_for_uri(uri,None)
        return None

    def failed(self,view,event,uri,error):
        if error.matches(WebKit2.network_error_quark(),WebKit2.NetworkError.CANCELLED):
            return False
        view.load_html('<html><body style="font-family:sans-serif;padding:60px;color:#375e4b"><h2>工具正在准备中</h2><p>稍候点击窗口右上角刷新。如果持续无法打开，请运行 SearchTools 的诊断命令。</p></body></html>',uri)
        return True

    def loaded(self,view,event):
        if event==WebKit2.LoadEvent.FINISHED:self.update_zoom_label()
        if os.getenv('SEARCHTOOLS_SMOKE') and event==WebKit2.LoadEvent.FINISHED:
            print('LOADED',view.get_uri(),flush=True)

    def smoke(self):
        original=self.stack.get_visible_child_name()
        for name,view in self.views.items():
            self.stack.set_visible_child_name(name)
            before=view.get_zoom_level()
            self.zoom('in')
            assert abs(view.get_zoom_level()-min(2,round(before+.1,2)))<.001
            view.set_zoom_level(before)
            self.zoom_levels[name]=before
            print('ZOOM_OK',name,before,flush=True)
        self.zoom_file.write_text(json.dumps(self.zoom_levels))
        self.stack.set_visible_child_name(original)
        self.update_zoom_label()
        for name,view in self.views.items():
            view.run_javascript('JSON.stringify({title:document.title,text:document.body.innerText.slice(0,180)})',None,lambda v,r,n:print(n,v.run_javascript_finish(r).get_js_value().to_string(),flush=True),name)
        GLib.timeout_add_seconds(2,lambda:self.quit() or False)
        return False

    def download(self,context,download):
        def destination(dl,name):
            dialog=Gtk.FileChooserDialog(title='保存文件',transient_for=self.window,action=Gtk.FileChooserAction.SAVE)
            dialog.add_buttons('取消',Gtk.ResponseType.CANCEL,'保存',Gtk.ResponseType.OK)
            dialog.set_current_name(Path(name).name)
            dialog.set_do_overwrite_confirmation(True)
            if dialog.run()==Gtk.ResponseType.OK:
                dl.set_destination(Path(dialog.get_filename()).as_uri())
                dl.set_allow_overwrite(True)
            else:
                dl.cancel()
            dialog.destroy()
            return True
        download.connect('decide-destination',destination)

if __name__=='__main__':
    raise SystemExit(Desktop().run([sys.argv[0]]))
