"""Publish Provider 协议（S7 发布引擎）。

``PublishProvider`` 为结构化协议（PEP 544，``runtime_checkable``），照
:mod:`worker.runtime.providers.image.base` 的范式：新增渠道只需满足
``name`` + ``probe`` 签名，不必改动 dispatch。

**为什么这一层必须先有接口**（与 S2 先定 ``ImageProvider`` 同一个理由）：
ADR-012 把 OpenCLI 定为底座候选，但它只是**候选** —— 重依赖（Node ≥ 20
+ 全局 npm 包 + 浏览器扩展 + 常驻 daemon），且适配器会随站点改版失效
（上游自己都要 ``autofix`` 修）。先写死某一家，接口就会被它的参数形状带偏
（命令名、选项名、退出码约定），它一改名就等于重写。

**这一轮只定 ``probe``，不定 ``fill``** —— 契约照**已核实的事实**写，不照
想象写。``probe`` 的形状来自 ADR-012 已核实的两件事：``opencli doctor``
子命令、以及它按 ``sysexits.h`` 语义返回退出码。而 ``fill`` 的参数形状取决于
各家 ``draft`` 命令的**实际**选项，没在真机上核实过就不写死 —— 签名一旦定错，
比没有签名更坏：后来者会照着一个错的契约去实现。真机验过再按同一范式补。

若将来要为「填充完停在哪」加能力，注意 ADR-008 的边界（见下）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final, NamedTuple, Protocol, runtime_checkable


class AvailabilityState(StrEnum):
    """发布 Provider 的三态可用性。

    **刻意只有三态** —— 不允许「大概能用」这种回答。ADR-012 要求未装 /
    daemon 未起 / 未登录一律**显式**报出，不静默降级；而多出来的中间态
    正是静默降级藏身的地方（「部分可用」最后一定会被当成可用）。
    """

    #: 桥通了，可以填充
    READY = "ready"
    #: 依赖缺失或桥断：没装 / daemon 未起 / 配置错
    UNAVAILABLE = "unavailable"
    #: 装好了、桥也通，但浏览器会话未登录 —— 用户做一步就能好
    NEED_LOGIN = "need_login"


#: ``sysexits.h`` 退出码（借鉴 OpenCLI 的**语义**，ADR-012；只借鉴不引代码）。
#:
#: 外部工具用**退出码**表达「可操作状态」，比解析 stderr 文本可靠得多 ——
#: 文本会翻译、会随版本改措辞，退出码是它的对外接口。这里只登记我们真的会
#: 分支的几个，其余一律落 ``UNAVAILABLE``（不认识的就当不可用，不猜）。
EX_OK: Final = 0
#: 查得到，但结果为空
EX_NOINPUT: Final = 66
#: 服务不可用：daemon 未起 / 依赖缺失
EX_UNAVAILABLE: Final = 69
#: 暂时失败：超时
EX_TEMPFAIL: Final = 75
#: 权限不足：未登录
EX_NOPERM: Final = 77
#: 配置错
EX_CONFIG: Final = 78


def classify_exit(code: int | None) -> AvailabilityState:
    """把外部工具的退出码翻译成三态。

    ``66``（结果为空）算 ``READY``：**桥是通的**，只是这次没东西可报。
    把它当不可用会让人去修一个没坏的桥 —— 这是最容易犯的一类误判。

    ``None``（进程被超时杀掉、拿不到退出码）算 ``UNAVAILABLE``：不确定可用
    就当不可用。宁可让用户多看一眼，也不假装能填 —— 假装的代价是用户以为
    排上了、实际什么都没发生。
    """
    if code in (EX_OK, EX_NOINPUT):
        return AvailabilityState.READY
    if code == EX_NOPERM:
        return AvailabilityState.NEED_LOGIN
    return AvailabilityState.UNAVAILABLE


class Availability(NamedTuple):
    """一次可用性探测的结果。

    刻意做成**可读**的四元组：``detail`` 说「现在是什么」，``hint`` 说
    「你要做什么」。只回一个状态码等于让用户自己猜，而本项目的要求是
    「错误要教人怎么修」—— 探测未通过时 ``hint`` 必须给出**可执行的一步**
    （装什么、开什么、登什么），不能是「请检查配置」。
    """

    state: AvailabilityState
    #: 探的是哪个渠道（未配置时为 ``""``）
    provider: str
    #: 现在是什么状态（可读，面向用户）
    detail: str
    #: 你该做什么（可执行的一步）
    hint: str
    #: 外部工具原始退出码，仅用于诊断；``None`` = 没拿到（超时被杀 / 没装）
    exit_code: int | None = None


@runtime_checkable
class PublishProvider(Protocol):
    """发布 Provider 协议。

    ⛔ **这个协议里没有、也不会有 ``publish``。** ADR-008 规定 V0.1–V0.5
    只允许 FILL_AND_PREVIEW（填完停在预览页，最终点发布由用户手动完成）。
    把这条约束写成「有 ``publish`` 方法但调用处拦住」，是把承诺留在注释里 ——
    拦住一处调用，拦不住下一处。写成**协议里根本不存在这个动作**，才是把它
    变成事实：结构性缺失无法被绕过。

    这与 ``scripts/check_mcp_surface.py`` 的 E 检查项同源 —— 那里把
    「``update_config`` 永不注册」从 docstring 承诺变成可执行断言，这里把
    「不自动发布」变成协议层面的缺失。同一个原则：
    **安全保证要么是可执行的，要么就是没有。**
    """

    name: str

    async def probe(self) -> Availability:
        """探测本 Provider 当前是否可用（三态）。

        实现**不得用异常表达「不可用」**：不可用是正常结果，走返回值；
        异常只留给「探测这件事本身坏了」（如意外 ``OSError``）。理由同
        :class:`Availability` —— handler 拿到异常只能转成一句干巴巴的
        ``UNAVAILABLE``，而用户需要的是「怎么修」。

        Returns:
            三态之一，附可读原因与可操作建议。
        """
        ...
