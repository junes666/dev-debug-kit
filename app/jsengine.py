"""基于 quickjs 的离线 JS 引擎封装。

提供：
  - format_js / minify_js ：复用内置 beautify.js / terser（在 quickjs 里跑）
  - run(code, expr, env)  ：在纯 V8(ECMAScript) 或 浏览器模拟 环境里执行用户代码，
                            捕获 console 输出、执行表达式结果、并解析出变量/函数/类的作用域树。
"""
from __future__ import annotations

import json
import pathlib
import quickjs

_LIB = pathlib.Path(__file__).resolve().parent.parent / "lib"

# --------------------------------------------------------------------------- #
#  JS 侧公共前奏：console 捕获 + 变量解析 helper
# --------------------------------------------------------------------------- #
_PRELUDE = r"""
var __LOGS = [];                       // 运行期日志缓冲（结束后一次性读回，避免运行中回调 Python）
function __ser(v, depth){
  depth = depth || 0;
  try{
    if (v === null) return "null";
    if (v === undefined) return "undefined";
    var t = typeof v;
    if (t === "string") return depth === 0 ? v : JSON.stringify(v);
    if (t === "number" || t === "boolean" || t === "bigint") return String(v);
    if (t === "symbol") return v.toString();
    if (t === "function") {
      var s = Function.prototype.toString.call(v);
      if (/^class[\s{]/.test(s)) return "class " + (v.name || "");
      var m = s.replace(/\s+/g," ").match(/\(([^)]*)\)/);
      return "ƒ " + (v.name || "anonymous") + "(" + (m ? m[1].trim() : "") + ")";
    }
    if (v instanceof Error) return v.name + ": " + v.message;
    if (Array.isArray(v)) {
      if (depth > 3) return "[…]";
      return "[" + v.slice(0, 100).map(function(x){return __ser(x, depth+1)}).join(", ") + "]";
    }
    if (t === "object") {
      if (depth > 3) return "{…}";
      var ks = Object.keys(v).slice(0, 100);
      return "{" + ks.map(function(k){return k + ": " + __ser(v[k], depth+1)}).join(", ") + "}";
    }
    return String(v);
  }catch(e){ return "[unserializable]"; }
}
function __emit(level){
  return function(){
    var parts = [];
    for (var i=0;i<arguments.length;i++) parts.push(__ser(arguments[i], 0));
    __LOGS.push({ level: level, text: parts.join(" ") });
  };
}
var console = {
  log: __emit("log"), info: __emit("info"), warn: __emit("warn"),
  error: __emit("error"), debug: __emit("debug"), trace: __emit("log"),
  dir: __emit("log"), table: __emit("log")
};

/* ---- 作用域解析 ---- */
var __SCOPE = [];
function __kindOf(v){
  if (v === null) return "null";
  if (Array.isArray(v)) return "array";
  var t = typeof v;
  if (t === "function") return /^class[\s{]/.test(Function.prototype.toString.call(v)) ? "class" : "function";
  return t;               // object / string / number / boolean / undefined / symbol / bigint
}
function __sig(name, v){
  var t = typeof v;
  if (t === "function"){
    var s = Function.prototype.toString.call(v);
    if (/^class[\s{]/.test(s)) return "class " + name;
    var m = s.replace(/\s+/g," ").match(/\(([^)]*)\)/);
    return name + "(" + (m ? m[1].trim() : "") + ")";
  }
  return name;
}
function __membersOf(v){
  var out = [];
  try{
    var k = __kindOf(v);
    if (k === "class"){
      var proto = v.prototype || {};
      Object.getOwnPropertyNames(proto).forEach(function(n){
        if (n === "constructor") return;
        var d = Object.getOwnPropertyDescriptor(proto, n);
        var kind = (d && (d.get || d.set)) ? "getter" : (typeof proto[n] === "function" ? "method" : "field");
        out.push({name:n, kind:kind, sig: kind==="method" ? __sig(n, proto[n]) : n});
      });
      Object.getOwnPropertyNames(v).forEach(function(n){
        if (["length","name","prototype"].indexOf(n) >= 0) return;
        out.push({name:n, kind:"static", sig:"static " + __sig(n, v[n])});
      });
    } else if (k === "function"){
      // 展示参数
      var s = Function.prototype.toString.call(v).replace(/\s+/g," ");
      var m = s.match(/\(([^)]*)\)/);
      var params = m && m[1].trim() ? m[1].split(",") : [];
      params.forEach(function(pp){ out.push({name:pp.trim(), kind:"param", sig:pp.trim()}); });
    } else if (k === "object"){
      Object.keys(v).slice(0, 200).forEach(function(n){
        out.push({name:n, kind:"prop", sig:n, preview:__ser(v[n],1).slice(0,120)});
      });
    } else if (k === "array"){
      v.slice(0, 200).forEach(function(x,i){
        out.push({name:"["+i+"]", kind:"item", sig:"["+i+"]", preview:__ser(x,1).slice(0,120)});
      });
    }
  }catch(e){}
  return out;
}
function __push(name, v){
  try{
    __SCOPE.push({ name:name, kind:__kindOf(v), sig:__sig(name,v),
                   preview:__ser(v,0).slice(0,200), members:__membersOf(v) });
  }catch(e){ __SCOPE.push({name:name, kind:"unknown", sig:name, preview:"", members:[]}); }
}
"""

_BROWSER_SHIM = r"""
var window = globalThis; var self = globalThis; var top = globalThis; var parent = globalThis;
var navigator = { userAgent:"DevDebug/1.0 (QuickJS browser-sim)", language:"zh-CN",
                  platform:"DevDebug", onLine:false, languages:["zh-CN","en"] };
var location = { href:"about:blank", protocol:"about:", host:"", hostname:"", pathname:"blank",
                 search:"", hash:"", origin:"null", reload:function(){}, assign:function(){}, replace:function(){} };
function __Storage(){ var m={}; return {
  getItem:function(k){ return (k in m)? m[k]: null; }, setItem:function(k,v){ m[k]=String(v); },
  removeItem:function(k){ delete m[k]; }, clear:function(){ m={}; },
  key:function(i){ return Object.keys(m)[i]||null; }, get length(){ return Object.keys(m).length; } }; }
var localStorage = __Storage(); var sessionStorage = __Storage();
var __B64="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
function btoa(s){ var o="",i=0; s=String(s); while(i<s.length){ var c1=s.charCodeAt(i++),c2=s.charCodeAt(i++),c3=s.charCodeAt(i++);
  var e1=c1>>2,e2=((c1&3)<<4)|(c2>>4),e3=((c2&15)<<2)|(c3>>6),e4=c3&63;
  if(isNaN(c2)){e3=e4=64;}else if(isNaN(c3)){e4=64;} o+=__B64.charAt(e1)+__B64.charAt(e2)+__B64.charAt(e3)+__B64.charAt(e4);} return o; }
function atob(s){ s=String(s).replace(/[^A-Za-z0-9+/]/g,""); var o="",i=0; while(i<s.length){
  var e1=__B64.indexOf(s.charAt(i++)),e2=__B64.indexOf(s.charAt(i++)),e3=__B64.indexOf(s.charAt(i++)),e4=__B64.indexOf(s.charAt(i++));
  var c1=(e1<<2)|(e2>>4),c2=((e2&15)<<4)|(e3>>2),c3=((e3&3)<<6)|e4;
  o+=String.fromCharCode(c1); if(e3!==64)o+=String.fromCharCode(c2); if(e4!==64)o+=String.fromCharCode(c3);} return o; }
function __node(tag){ return { tagName:(tag||"div").toUpperCase(), nodeName:(tag||"div").toUpperCase(),
  style:{}, dataset:{}, className:"", id:"", children:[], childNodes:[], attributes:{}, innerHTML:"", textContent:"",
  setAttribute:function(k,v){ this.attributes[k]=v; }, getAttribute:function(k){ return this.attributes[k]; },
  appendChild:function(c){ this.children.push(c); return c; }, removeChild:function(c){ return c; },
  addEventListener:function(){}, removeEventListener:function(){}, querySelector:function(){ return null; },
  querySelectorAll:function(){ return []; }, classList:{ add:function(){}, remove:function(){}, toggle:function(){}, contains:function(){return false;} } }; }
var document = { title:"", cookie:"", readyState:"complete", body:__node("body"), documentElement:__node("html"),
  createElement:function(t){ return __node(t); }, createTextNode:function(t){ return {textContent:t}; },
  getElementById:function(){ return null; }, getElementsByClassName:function(){ return []; },
  getElementsByTagName:function(){ return []; }, querySelector:function(){ return null; },
  querySelectorAll:function(){ return []; }, addEventListener:function(){}, removeEventListener:function(){} };
function alert(m){ console.log("[alert]", m); }
function prompt(m,d){ console.log("[prompt]", m); return d||null; }
function confirm(m){ console.log("[confirm]", m); return true; }
/* 简易定时器：加入队列，主程序执行后统一冲刷 */
var __timers=[], __tid=1;
function setTimeout(fn,ms){ __timers.push({fn:fn,ms:ms||0,id:__tid,args:[].slice.call(arguments,2)}); return __tid++; }
function setInterval(fn,ms){ __timers.push({fn:fn,ms:ms||0,id:__tid,args:[].slice.call(arguments,2)}); return __tid++; }
function clearTimeout(id){ __timers=__timers.filter(function(t){return t.id!==id;}); }
function clearInterval(id){ clearTimeout(id); }
function queueMicrotask(fn){ Promise.resolve().then(fn); }
function __drainTimers(max){ var n=0; __timers.sort(function(a,b){return a.ms-b.ms;});
  while(__timers.length && n<max){ var t=__timers.shift(); try{ t.fn.apply(null,t.args); }catch(e){ console.error(e); } n++; } }
function fetch(){ return Promise.reject(new Error("浏览器模拟环境无网络，请用 HTTP 调试面板发请求")); }
function XMLHttpRequest(){ throw new Error("浏览器模拟环境不支持 XMLHttpRequest，请用 HTTP 调试面板"); }
function requestAnimationFrame(fn){ return setTimeout(function(){ fn(16); }, 16); }
function cancelAnimationFrame(id){ clearTimeout(id); }
"""


class JsEngine:
    def __init__(self):
        self._fmt = None

    # ---- 格式化 / 压缩 -------------------------------------------------- #
    def _fmt_ctx(self):
        if self._fmt is None:
            c = quickjs.Context()
            c.eval("var global=(function(){return this})()||{}; var self=global; var window=global;")
            c.eval((_LIB / "beautify.js").read_text(encoding="utf-8"))
            c.eval((_LIB / "terser.min.js").read_text(encoding="utf-8"))
            self._fmt = c
        return self._fmt

    def format_js(self, code: str, indent: int = 2) -> str:
        c = self._fmt_ctx()
        c.set("__src", code)
        opts = json.dumps({"indent_size": indent, "space_in_empty_paren": True,
                           "preserve_newlines": True, "max_preserve_newlines": 2, "brace_style": "collapse"})
        return c.eval(f"js_beautify(__src, {opts})")

    def minify_js(self, code: str) -> str:
        c = self._fmt_ctx()
        c.set("__src", code)
        c.eval("""
            globalThis.__mres=null; globalThis.__merr=null; globalThis.__mdone=false;
            Terser.minify(__src, {compress:true, mangle:true})
              .then(function(r){ __mres=r.code; __mdone=true; })
              .catch(function(e){ __merr=String(e); __mdone=true; });
        """)
        for _ in range(500000):
            if c.eval("__mdone"):
                break
            try:
                c.execute_pending_job()
            except Exception:
                break
        err = c.eval("__merr")
        if err:
            raise ValueError(err)
        return c.eval("__mres") or ""

    # ---- 运行 ----------------------------------------------------------- #
    def run(self, code: str, expr: str = "", env: str = "v8", timeout: float = 3.0) -> dict:
        c = quickjs.Context()
        try:
            c.set_memory_limit(256 * 1024 * 1024)
            c.set_time_limit(max(0.2, timeout))
        except Exception:
            pass

        result = {"logs": [], "result": None, "result_type": None, "error": None, "scope": []}
        try:
            if env == "browser":
                c.eval(_BROWSER_SHIM)
            c.eval(_PRELUDE)
        except Exception as e:  # 前奏出错（几乎不会）
            result["error"] = f"引擎初始化失败: {e}"
            return result

        names = _declared_names(code)
        collector = "".join(f'try{{__push({json.dumps(n)},{n})}}catch(__e){{}}\n' for n in names)
        wrapped_expr = ""
        if expr.strip():
            wrapped_expr = f"\n;globalThis.__RET=({expr});globalThis.__HASRET=true;"

        program = f"{code}\n;{collector}\nglobalThis.__SCOPE_JSON=JSON.stringify(__SCOPE);{wrapped_expr}"
        try:
            c.eval(program)
            if env == "browser":
                try:
                    c.eval("__drainTimers(2000)")
                except Exception:
                    pass
            # 冲刷微任务 / Promise
            for _ in range(20000):
                try:
                    if not c.execute_pending_job():
                        break
                except Exception:
                    break
        except Exception as e:
            result["error"] = _clean_err(str(e))

        # 日志（一次性读回）
        try:
            result["logs"] = json.loads(c.eval("JSON.stringify(__LOGS || [])"))
        except Exception:
            result["logs"] = []
        # 作用域
        try:
            sj = c.eval("globalThis.__SCOPE_JSON || '[]'")
            result["scope"] = json.loads(sj)
        except Exception:
            result["scope"] = []
        # 表达式结果
        try:
            if c.eval("!!globalThis.__HASRET"):
                result["result"] = c.eval("__ser(globalThis.__RET, 0)")
                result["result_type"] = c.eval("__kindOf(globalThis.__RET)")
        except Exception as e:
            if not result["error"]:
                result["error"] = _clean_err(str(e))
        return result


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #
import re

_DECL_RE = re.compile(
    r"""^[ \t]*(?:export[ \t]+)?(?:default[ \t]+)?
        (?:async[ \t]+)?
        (?:function\*?[ \t]+(?P<fn>[A-Za-z_$][\w$]*)
         |class[ \t]+(?P<cls>[A-Za-z_$][\w$]*)
         |(?:let|const|var)[ \t]+(?P<var>[A-Za-z_$][\w$]*))""",
    re.MULTILINE | re.VERBOSE,
)


def _declared_names(code: str) -> list[str]:
    seen, out = set(), []
    for m in _DECL_RE.finditer(code):
        name = m.group("fn") or m.group("cls") or m.group("var")
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _clean_err(msg: str) -> str:
    return msg.strip()
