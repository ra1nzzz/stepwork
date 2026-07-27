/**
 * 原子件的行为约定。
 *
 * 这些组件的价值在于「同一种状态在所有页面长得一样」，所以测的是**语义
 * 契约**（错误优先于空态、tone 与语义绑定），不是 DOM 细节。
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AsyncSection, EmptyState, NoticeBar, StatusBadge } from "./primitives";

describe("AsyncSection 三态顺序", () => {
  it("加载中优先于一切", () => {
    render(
      <AsyncSection isLoading error={new Error("boom")} isEmpty empty={<p>空</p>}>
        <p>内容</p>
      </AsyncSection>,
    );
    expect(screen.getByText("加载中…")).toBeInTheDocument();
    expect(screen.queryByText("空")).not.toBeInTheDocument();
  });

  it("错误优先于空态 —— 这是本组件存在的主要理由", () => {
    // 手写三态时常见的顺序错误：先判空再判错，于是请求失败时显示
    // 「暂无数据」，用户以为真的没数据，不会去重试。
    render(
      <AsyncSection isLoading={false} error={new Error("连接失败")} isEmpty empty={<p>暂无数据</p>}>
        <p>内容</p>
      </AsyncSection>,
    );
    expect(screen.getByText("连接失败")).toBeInTheDocument();
    expect(screen.queryByText("暂无数据")).not.toBeInTheDocument();
  });

  it("无错且为空时走空态", () => {
    render(
      <AsyncSection isLoading={false} isEmpty empty={<p>暂无数据</p>}>
        <p>内容</p>
      </AsyncSection>,
    );
    expect(screen.getByText("暂无数据")).toBeInTheDocument();
  });

  it("有数据时渲染子内容", () => {
    render(
      <AsyncSection isLoading={false} isEmpty={false} empty={<p>暂无数据</p>}>
        <p>内容</p>
      </AsyncSection>,
    );
    expect(screen.getByText("内容")).toBeInTheDocument();
  });

  it("非 Error 类型的错误也要有可读输出", () => {
    render(
      <AsyncSection isLoading={false} error="字符串错误">
        <p>内容</p>
      </AsyncSection>,
    );
    expect(screen.getByText("字符串错误")).toBeInTheDocument();
  });
});

describe("EmptyState", () => {
  it("除了标题还应给出下一步提示", () => {
    render(<EmptyState title="还没有素材" hint="拖入视频或点击选择文件" />);
    expect(screen.getByText("还没有素材")).toBeInTheDocument();
    expect(screen.getByText("拖入视频或点击选择文件")).toBeInTheDocument();
  });

  it("没有 hint 时不渲染空段落", () => {
    const { container } = render(<EmptyState title="空" />);
    expect(container.querySelectorAll(".empty-sub")).toHaveLength(0);
  });
});

describe("NoticeBar / StatusBadge 的 tone 与语义绑定", () => {
  it("danger 走错误样式，其余走普通说明", () => {
    const { container, rerender } = render(<NoticeBar tone="danger">出错了</NoticeBar>);
    expect(container.querySelector(".error-text")).not.toBeNull();
    rerender(<NoticeBar tone="info">一般说明</NoticeBar>);
    expect(container.querySelector(".error-text")).toBeNull();
  });

  it("tone 落到 data-tone 上，便于按语义而非颜色定位", () => {
    const { container } = render(<NoticeBar tone="warning">注意</NoticeBar>);
    expect(container.querySelector('[data-tone="warning"]')).not.toBeNull();
  });

  it("StatusBadge 默认 info，可带 title 说明", () => {
    render(<StatusBadge title="解释文字">已启用</StatusBadge>);
    expect(screen.getByTitle("解释文字")).toHaveTextContent("已启用");
  });
});
