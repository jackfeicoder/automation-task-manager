"""Optional direct-network checks, disabled by default."""
import base64
import ctypes
from ctypes import wintypes
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import threading
import time
from urllib.parse import urlsplit

VPN_NAMES = re.compile(r'clash|mihomo|v2ray|v2rayn|xray|sing.?box|hiddify|nekoray|nekobox|'
    r'wireguard|wintun|openvpn|tailscale|zerotier|shadowsocks|surfshark|nordvpn|'
    r'expressvpn|protonvpn|forticlient|anyconnect|globalprotect|warp|tun2socks|trojan', re.I)
ADAPTER_NAMES = re.compile(r'\bvpn\b|\btap\b|\btun\b|wintun|wireguard|clash|mihomo|'
    r'tailscale|zerotier|openvpn|anyconnect|fortinet|globalprotect|warp', re.I)
PROXY_ENV = {'http_proxy', 'https_proxy', 'all_proxy', 'ftp_proxy'}
GEO_URLS = ('https://ipwho.is/', 'https://ipapi.co/json/')

SNAPSHOT_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$failures = @()
$vpn = @()
try { $vpn += @(Get-VpnConnection | Where-Object {$_.ConnectionStatus -ne 'Disconnected'} | Select-Object -ExpandProperty Name) }
catch { $failures += 'vpn-user' }
try { $vpn += @(Get-VpnConnection -AllUserConnection | Where-Object {$_.ConnectionStatus -ne 'Disconnected'} | Select-Object -ExpandProperty Name) }
catch { $failures += 'vpn-machine' }
$adapters = @()
$routes = @()
try { $adapters = @(Get-NetAdapter -IncludeHidden | Where-Object {$_.Status -eq 'Up'} | Select-Object Name,InterfaceDescription,ifIndex,HardwareInterface,InterfaceType) }
catch { $failures += 'adapters' }
try { $routes = @(Get-NetRoute -PolicyStore ActiveStore | Where-Object {$_.DestinationPrefix -in @('0.0.0.0/0','::/0','0.0.0.0/1','128.0.0.0/1','::/1','8000::/1')} | Select-Object DestinationPrefix,NextHop,InterfaceIndex,RouteMetric) }
catch { $failures += 'routes' }
@{failures=$failures;vpn=$vpn;adapters=$adapters;routes=$routes} | ConvertTo-Json -Depth 5 -Compress
'''


class NetworkBlocked(RuntimeError):
    pass


def proxy_reasons(env):
    reasons = []
    if any(key.lower() in PROXY_ENV and value.strip() for key, value in env.items()):
        reasons.append('检测到代理环境变量')
    if os.name != 'nt':
        return reasons + ['网络检查目前仅支持 Windows']
    import winreg

    def value(hive, path, name, default=None):
        try:
            with winreg.OpenKey(hive, path) as key:
                return winreg.QueryValueEx(key, name)[0]
        except FileNotFoundError:
            return default
        except OSError:
            raise NetworkBlocked('读取 Windows 代理配置失败') from None

    internet = r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'
    for hive, path in ((winreg.HKEY_CURRENT_USER, 'Environment'),
            (winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment')):
        for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'FTP_PROXY'):
            if value(hive, path, name, ''):
                reasons.append('Windows 用户或系统环境中配置了代理变量')
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        if value(hive, internet, 'ProxyEnable', 0):
            reasons.append('Windows 系统代理已开启')
        if value(hive, internet, 'AutoConfigURL', ''):
            reasons.append('Windows PAC 自动代理已配置')
        flags = value(hive, internet + r'\Connections', 'DefaultConnectionSettings', b'')
        if value(hive, internet, 'AutoDetect', 0) or (isinstance(flags, bytes) and len(flags) > 8 and flags[8] & 8):
            reasons.append('Windows 自动检测代理已开启，请先关闭')
        policy = r'Software\Policies\Microsoft\Edge'
        if value(hive, policy, 'ProxyMode', 'direct') != 'direct' or value(hive, policy, 'ProxyServer', '') or value(hive, policy, 'ProxyPacUrl', ''):
            reasons.append('Edge 管理策略中存在代理配置')
        try:
            with winreg.OpenKey(hive, policy + r'\ExtensionInstallForcelist') as key:
                if winreg.QueryInfoKey(key)[1]:
                    reasons.append('Edge 存在强制扩展，请先核对扩展网络权限')
        except FileNotFoundError:
            pass
        except OSError:
            raise NetworkBlocked('读取 Edge 扩展策略失败') from None

    class ProxyInfo(ctypes.Structure):
        _fields_ = [('access', wintypes.DWORD), ('proxy', ctypes.c_void_p), ('bypass', ctypes.c_void_p)]
    info = ProxyInfo()
    winhttp = ctypes.WinDLL('winhttp', use_last_error=True)
    winhttp.WinHttpGetDefaultProxyConfiguration.argtypes = [ctypes.POINTER(ProxyInfo)]
    winhttp.WinHttpGetDefaultProxyConfiguration.restype = wintypes.BOOL
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GlobalFree.argtypes = [ctypes.c_void_p]
    kernel.GlobalFree.restype = ctypes.c_void_p
    if not winhttp.WinHttpGetDefaultProxyConfiguration(ctypes.byref(info)):
        raise NetworkBlocked('读取 WinHTTP 代理配置失败')
    try:
        if info.access != 1 or info.proxy:
            reasons.append('WinHTTP 代理已配置')
    finally:
        for pointer in (info.proxy, info.bypass):
            if pointer:
                kernel.GlobalFree(pointer)
    return reasons


def windows_process_names():
    # A process snapshot contains executable names without opening each process.
    # psutil can return None for protected Windows processes at normal privileges.
    if os.name != 'nt':
        raise NetworkBlocked('进程检查目前仅支持 Windows')

    class ProcessEntry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
            ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
            ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
            ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
            ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for function in (kernel.Process32FirstW, kernel.Process32NextW):
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        function.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if handle in (None, ctypes.c_void_p(-1).value):
        raise NetworkBlocked('读取进程快照失败，停止任务')
    names = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel.Process32FirstW(handle, ctypes.byref(entry)):
            raise NetworkBlocked('读取进程快照失败，停止任务')
        while True:
            name = entry.szExeFile.strip()
            if not name:
                raise NetworkBlocked('进程快照包含未知名称，停止任务')
            names.append(name)
            if not kernel.Process32NextW(handle, ctypes.byref(entry)):
                if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                    raise NetworkBlocked('进程快照读取中断，停止任务')
                break
    finally:
        kernel.CloseHandle(handle)
    return names


def process_reasons():
    try:
        names = windows_process_names()
    except OSError:
        raise NetworkBlocked('读取进程列表失败，停止任务') from None
    if any(VPN_NAMES.search(name) for name in names):
        return ['检测到运行中的 VPN / 代理程序，请退出后运行']
    return []


def local_snapshot(env):
    reasons = proxy_reasons(env) + process_reasons()
    if reasons:
        raise NetworkBlocked('；'.join(dict.fromkeys(reasons)))
    encoded = base64.b64encode(SNAPSHOT_SCRIPT.encode('utf-16-le')).decode()
    powershell = Path(os.environ.get('SYSTEMROOT', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, creationflags=0x08000000, check=True)
        snapshot = json.loads(result.stdout.decode('utf-8-sig'))
    except (OSError, subprocess.SubprocessError, ValueError):
        raise NetworkBlocked('读取 VPN / 网卡 / 路由信息失败，停止任务') from None
    if snapshot.get('failures'):
        reasons.append('部分 Windows 网络检查未完成，停止任务')
    if snapshot.get('vpn'):
        reasons.append('Windows VPN 连接未断开')
    adapters = snapshot.get('adapters', [])
    for adapter in adapters:
        label = str(adapter.get('Name', '')) + ' ' + str(adapter.get('InterfaceDescription', ''))
        if ADAPTER_NAMES.search(label) or adapter.get('InterfaceType') in (23, 131):
            reasons.append('检测到活动 VPN / 隧道网卡')
    routes = snapshot.get('routes', [])
    if not routes:
        reasons.append('未检测到有效默认路由')
    for route in routes:
        if route['DestinationPrefix'] not in ('0.0.0.0/0', '::/0'):
            reasons.append('检测到分流隧道路由')
        matches = [a for a in adapters if a['ifIndex'] == route['InterfaceIndex']]
        if not matches or any(not a.get('HardwareInterface') for a in matches):
            reasons.append('默认路由经过非物理或未知网卡')
    if reasons:
        raise NetworkBlocked('；'.join(dict.fromkeys(reasons)))
    # Only stable fields are compared; dynamic route lifetimes are excluded.
    return json.dumps({'adapters': sorted(adapters, key=lambda x: x['ifIndex']),
        'routes': sorted(routes, key=lambda x: (x['DestinationPrefix'], x['InterfaceIndex'], x['NextHop']))}, sort_keys=True)


def direct_json(url, family):
    class DirectHTTPS(http.client.HTTPSConnection):
        def connect(self):
            # Check each usable IP family; browser IPv6 must not go unchecked.
            addresses = socket.getaddrinfo(self.host, self.port, family, socket.SOCK_STREAM)
            last_error = None
            for af, kind, protocol, _, endpoint in addresses:
                connection = socket.socket(af, kind, protocol)
                connection.settimeout(self.timeout)
                try:
                    connection.connect(endpoint)
                    self.sock = ssl.create_default_context().wrap_socket(connection, server_hostname=self.host)
                    return
                except OSError as error:
                    connection.close()
                    last_error = error
            raise OSError('Direct HTTPS connection failed') from last_error
    parsed = urlsplit(url)
    connection = DirectHTTPS(parsed.hostname, timeout=10)
    try:
        connection.request('GET', parsed.path or '/', headers={'User-Agent': 'TaskHarbor-NetworkCheck/1.0'})
        response = connection.getresponse()
        if response.status != 200:
            raise OSError('Geolocation unavailable')
        return json.loads(response.read(65536))
    finally:
        connection.close()


def check_country(expected, families):
    verified = {}
    for version in families:
        addresses = []
        for url in GEO_URLS:
            try:
                data = direct_json(url, socket.AF_INET if version == 4 else socket.AF_INET6)
                if data.get('success') is False or data.get('error'):
                    raise ValueError()
                country = data.get('country_code') or data.get('country')
                address = ipaddress.ip_address(data['ip'])
                if address.version != version or not address.is_global or not re.fullmatch('[A-Z]{2}', str(country)):
                    raise ValueError()
            except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
                raise NetworkBlocked(f'IPv{version} 出口地区查询失败或结果异常，停止任务') from None
            if country != expected:
                raise NetworkBlocked(f'IPv{version} 出口地区与预期地区不一致，停止任务')
            if any((data.get('security') or {}).get(key) is True for key in ('vpn', 'proxy', 'tor', 'anonymous')):
                raise NetworkBlocked('出口服务标记了 VPN / 代理，停止任务')
            addresses.append(str(address))
        if len(set(addresses)) != 1:
            raise NetworkBlocked('两个服务观测到不同出口，停止任务')
        verified[version] = addresses[0]
    return verified


class NetworkGuard:
    def __init__(self, env):
        self.env = env
        setting = env.get('REWARDS_REQUIRE_DIRECT_NETWORK', 'false').strip().lower()
        if setting not in ('false', 'true', '0', '1'):
            raise ValueError('REWARDS_REQUIRE_DIRECT_NETWORK 应为 true 或 false')
        self.enabled = setting in ('true', '1')
        self.country = env.get('REWARDS_COUNTRY_CODE', 'CN').strip().upper() or 'CN'
        if self.enabled and not re.fullmatch('[A-Z]{2}', self.country):
            raise NetworkBlocked('REWARDS_COUNTRY_CODE 应为两个字母的实际所在地区代码')
        self.error = None
        self.stop_event = threading.Event()
        self.thread = None
        self.last_country_check = 0

    def start(self):
        if not self.enabled:
            return
        self.baseline = local_snapshot(self.env)
        self.families = sorted({6 if route['DestinationPrefix'] == '::/0' else 4 for route in json.loads(self.baseline)['routes']})
        self.address = check_country(self.country, self.families)
        self.last_country_check = time.monotonic()
        self.check(full=True)
        self.thread = threading.Thread(target=self.monitor, daemon=True)
        self.thread.start()

    def check(self, full=False):
        if self.error:
            raise NetworkBlocked(self.error)
        if not self.enabled:
            return
        reasons = proxy_reasons(self.env) + process_reasons()
        if reasons:
            self.error = '；'.join(reasons)
            raise NetworkBlocked(self.error)
        if full and local_snapshot(self.env) != self.baseline:
            self.error = '运行期间网络或路由发生变化，停止任务'
            raise NetworkBlocked(self.error)

    def monitor(self):
        while not self.stop_event.wait(3):
            try:
                self.check(full=True)
                if time.monotonic() - self.last_country_check >= 60:
                    if check_country(self.country, self.families) != self.address:
                        raise NetworkBlocked('运行期间公网出口改变，停止任务')
                    self.last_country_check = time.monotonic()
            except NetworkBlocked as error:
                self.error = str(error)
                return
            except Exception:
                self.error = '运行期间网络复查失败，停止任务'
                return

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
