import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { usePolledResource } from "../hooks";

describe("Polling isolation", () => {
  afterEach(() => vi.useRealTimers());
  it("does not overlap requests and discards the response of a previous thread", async () => {
    vi.useFakeTimers();
    let resolveFirst!: (value: string) => void;
    const first = vi.fn(() => new Promise<string>(resolve => { resolveFirst = resolve; }));
    const second = vi.fn(async () => "second");
    const {result, rerender} = renderHook(({id}) => usePolledResource(id === "first" ? first : second, [id]), {initialProps: {id: "first"}});
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(first).toHaveBeenCalledTimes(1);
    rerender({id: "second"});
    await act(async () => {});
    expect(result.current.data).toBe("second");
    await act(async () => { resolveFirst("obsolete"); });
    expect(result.current.data).toBe("second");
  });
  it("keeps the last data and signals an error until a successful refresh", async () => {
    vi.useFakeTimers();
    const loader = vi.fn().mockResolvedValueOnce("current").mockRejectedValueOnce(new Error("offline")).mockResolvedValue("new");
    const {result} = renderHook(() => usePolledResource(loader, []));
    await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(result.current.data).toBe("current");
    expect(result.current.error).toBe("offline");
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(result.current.data).toBe("new");
    expect(result.current.error).toBeNull();
  });
});
