; ModuleID = 'attribution_controls.cpp'
source_filename = "attribution_controls.cpp"
target datalayout = "e-p:64:64-p1:64:64-p2:32:32-p3:32:32-p4:64:64-p5:32:32-p6:32:32-i64:64-v16:16-v24:32-v32:32-v48:64-v96:128-v192:256-v256:256-v512:512-v1024:1024-v2048:2048-n32:64-S32-A5-G1-ni:7"
target triple = "mxc-metax-macahca"

%struct.mcDevMallocInfo.0 = type { i32, i32, ptr }

@mcDeviceMemoryInfo = weak protected addrspace(1) externally_initialized global [1 x %struct.mcDevMallocInfo.0] zeroinitializer, align 8
@llvm.compiler.used = appending addrspace(1) global [1 x ptr] [ptr addrspacecast (ptr addrspace(1) @mcDeviceMemoryInfo to ptr)], section "llvm.metadata"

; Function Attrs: mustprogress noreturn nounwind
define weak void @__cxa_pure_virtual() local_unnamed_addr #0 {
entry:
  tail call void @llvm.trap()
  unreachable
}

; Function Attrs: cold noreturn nounwind memory(inaccessiblemem: write)
declare void @llvm.trap() #1

; Function Attrs: mustprogress noreturn nounwind
define weak void @__cxa_deleted_virtual() local_unnamed_addr #0 {
entry:
  tail call void @llvm.trap()
  unreachable
}

; Function Attrs: mustprogress nounwind
define weak protected void @__mcImplicitDeviceSynchronize() local_unnamed_addr #2 {
entry:
  %0 = tail call ptr addrspace(4) @llvm.mxc.implicitarg.ptr()
  %arrayidx.i.i = getelementptr inbounds i8, ptr addrspace(4) %0, i64 32
  %1 = load i64, ptr addrspace(4) %arrayidx.i.i, align 8, !tbaa !2
  %2 = inttoptr i64 %1 to ptr
  %arrayidx.i32.i = getelementptr inbounds i8, ptr addrspace(4) %0, i64 40
  %3 = load i64, ptr addrspace(4) %arrayidx.i32.i, align 8, !tbaa !2
  %4 = inttoptr i64 %3 to ptr
  %current_counter.i = getelementptr inbounds i8, ptr %4, i64 12
  %thread_counter.i = getelementptr inbounds i8, ptr %4, i64 16
  %5 = load ptr, ptr %thread_counter.i, align 8, !tbaa !6
  %cmp.not.i = icmp eq ptr %5, null
  br i1 %cmp.not.i, label %if.end.i, label %if.then.i

if.then.i:                                        ; preds = %entry
  %6 = tail call align 4 dereferenceable(64) ptr addrspace(4) @llvm.mxc.dispatch.ptr()
  %7 = getelementptr inbounds i8, ptr addrspace(4) %6, i64 12
  %8 = load i32, ptr addrspace(4) %7, align 4, !range !13, !invariant.load !14
  %9 = getelementptr inbounds i8, ptr addrspace(4) %6, i64 4
  %10 = load i32, ptr addrspace(4) %9, align 4, !invariant.load !14
  %conv.i14.i.i = and i32 %10, 65535
  %div.i15.i.i = udiv i32 %8, %conv.i14.i.i
  %mul.i16.i.i = mul i32 %div.i15.i.i, %conv.i14.i.i
  %cmp.i17.i.i = icmp ugt i32 %8, %mul.i16.i.i
  %conv2.i18.i.i = zext i1 %cmp.i17.i.i to i32
  %add.i19.i.i = add nuw i32 %div.i15.i.i, %conv2.i18.i.i
  %11 = getelementptr inbounds i8, ptr addrspace(4) %6, i64 16
  %12 = load i32, ptr addrspace(4) %11, align 4, !range !13, !invariant.load !14
  %13 = lshr i32 %10, 16
  %div.i.i.i = udiv i32 %12, %13
  %mul.i.i.i = mul i32 %div.i.i.i, %13
  %cmp.i.i.i = icmp ugt i32 %12, %mul.i.i.i
  %conv2.i.i.i = zext i1 %cmp.i.i.i to i32
  %add.i.i.i = add nuw i32 %div.i.i.i, %conv2.i.i.i
  %14 = tail call noundef range(i32 0, 2147483647) i32 @llvm.mxc.block.id.z(), !range !15
  %mul.i.i = mul i32 %add.i.i.i, %14
  %15 = tail call noundef range(i32 0, 2147483647) i32 @llvm.mxc.block.id.y(), !range !15
  %mul320.i.i = add i32 %mul.i.i, %15
  %add.i.i = mul i32 %mul320.i.i, %add.i19.i.i
  %16 = tail call noundef range(i32 0, 2147483647) i32 @llvm.mxc.block.id.x(), !range !15
  %add8.i.i = add i32 %add.i.i, %16
  %conv.i.i = zext i32 %add8.i.i to i64
  %arrayidx.i = getelementptr inbounds i32, ptr %5, i64 %conv.i.i
  %17 = tail call i1 @llvm.mxc.is.private(ptr nonnull %arrayidx.i)
  br label %while.cond.i

while.cond.i:                                     ; preds = %while.body.i, %if.then.i
  br i1 %17, label %if.then.i.i, label %if.end.i.i

if.then.i.i:                                      ; preds = %while.cond.i
  %18 = load i32, ptr %arrayidx.i, align 4, !tbaa !16
  br label %_Z9atomicAddPii.exit.i

if.end.i.i:                                       ; preds = %while.cond.i
  %19 = atomicrmw or ptr %arrayidx.i, i32 0 syncscope("device-one-as") monotonic, align 4
  br label %_Z9atomicAddPii.exit.i

_Z9atomicAddPii.exit.i:                           ; preds = %if.end.i.i, %if.then.i.i
  %retval.0.i.i = phi i32 [ %18, %if.then.i.i ], [ %19, %if.end.i.i ]
  %cmp6.i = icmp sgt i32 %retval.0.i.i, 0
  br i1 %cmp6.i, label %while.body.i, label %if.end.i

while.body.i:                                     ; preds = %_Z9atomicAddPii.exit.i
  tail call void @llvm.mxc.sleep(i32 1)
  br label %while.cond.i, !llvm.loop !17

if.end.i:                                         ; preds = %_Z9atomicAddPii.exit.i, %entry
  %20 = tail call i1 @llvm.mxc.is.private(ptr nonnull %current_counter.i)
  br i1 %20, label %if.then.i36.i, label %if.end.i34.i

if.then.i36.i:                                    ; preds = %if.end.i
  %21 = load i32, ptr %current_counter.i, align 4, !tbaa !16
  %add.i37.i = add nsw i32 %21, -1
  store i32 %add.i37.i, ptr %current_counter.i, align 4, !tbaa !16
  br label %_Z9atomicAddPii.exit38.i

if.end.i34.i:                                     ; preds = %if.end.i
  %22 = atomicrmw add ptr %current_counter.i, i32 -1 syncscope("device-one-as") monotonic, align 4
  br label %_Z9atomicAddPii.exit38.i

_Z9atomicAddPii.exit38.i:                         ; preds = %if.end.i34.i, %if.then.i36.i
  %retval.0.i35.i = phi i32 [ %21, %if.then.i36.i ], [ %22, %if.end.i34.i ]
  %call_deep.i = getelementptr inbounds i8, ptr %4, i64 4
  %23 = load i32, ptr %call_deep.i, align 4, !tbaa !19
  %cmp8.i = icmp eq i32 %23, 0
  br i1 %cmp8.i, label %if.then9.i, label %if.else.i

if.then9.i:                                       ; preds = %_Z9atomicAddPii.exit38.i
  %cmp10.i = icmp slt i32 %retval.0.i35.i, 2
  br i1 %cmp10.i, label %if.then11.i, label %__mcImplicitDeviceSynchronizeImpl.exit

if.then11.i:                                      ; preds = %if.then9.i
  tail call void @llvm.mxc.sleep(i32 30)
  br label %__mcImplicitDeviceSynchronizeImpl.exit

if.else.i:                                        ; preds = %_Z9atomicAddPii.exit38.i
  %parent_wrap13.i = getelementptr inbounds i8, ptr %4, i64 24
  %24 = load ptr, ptr %parent_wrap13.i, align 8, !tbaa !20
  %25 = load i32, ptr %4, align 8, !tbaa !21
  %thread_counter15.i = getelementptr inbounds i8, ptr %24, i64 16
  %26 = load ptr, ptr %thread_counter15.i, align 8, !tbaa !6
  %idxprom.i = zext i32 %25 to i64
  %arrayidx16.i = getelementptr inbounds i32, ptr %26, i64 %idxprom.i
  %cmp17.i = icmp slt i32 %retval.0.i35.i, 2
  br i1 %cmp17.i, label %if.then18.i, label %if.end20.i

if.then18.i:                                      ; preds = %if.else.i
  %mqlwrap_slots.i = getelementptr inbounds i8, ptr %2, i64 24
  %27 = load ptr, ptr %mqlwrap_slots.i, align 8, !tbaa !22
  %sub.ptr.rhs.cast.i = ptrtoint ptr %27 to i64
  %sub.ptr.sub.i = sub i64 %3, %sub.ptr.rhs.cast.i
  %sub.ptr.div.i = sdiv exact i64 %sub.ptr.sub.i, 96
  %mqlwrap_slot_mask.i = getelementptr inbounds i8, ptr %2, i64 80
  %28 = load i64, ptr %mqlwrap_slot_mask.i, align 8, !tbaa !24
  %29 = inttoptr i64 %28 to ptr
  %idx.ext.i.i = and i64 %sub.ptr.div.i, 4294967295
  %add.ptr.i.i = getelementptr inbounds i32, ptr %29, i64 %idx.ext.i.i
  %30 = tail call i1 @llvm.mxc.is.private(ptr %add.ptr.i.i)
  br i1 %30, label %if.then.i.i.i, label %if.end.i.i.i

if.then.i.i.i:                                    ; preds = %if.then18.i
  %31 = load i32, ptr %add.ptr.i.i, align 4, !tbaa !16
  %and.i.i.i = and i32 %31, 2147483647
  store i32 %and.i.i.i, ptr %add.ptr.i.i, align 4, !tbaa !16
  br label %if.end20.i

if.end.i.i.i:                                     ; preds = %if.then18.i
  %32 = atomicrmw and ptr %add.ptr.i.i, i32 2147483647 syncscope("device-one-as") monotonic, align 4
  br label %if.end20.i

if.end20.i:                                       ; preds = %if.end.i.i.i, %if.then.i.i.i, %if.else.i
  %33 = tail call i1 @llvm.mxc.is.private(ptr %arrayidx16.i)
  br i1 %33, label %if.then.i41.i, label %if.end.i39.i

if.then.i41.i:                                    ; preds = %if.end20.i
  %34 = load i32, ptr %arrayidx16.i, align 4, !tbaa !16
  %add.i42.i = add nsw i32 %34, -1
  store i32 %add.i42.i, ptr %arrayidx16.i, align 4, !tbaa !16
  br label %__mcImplicitDeviceSynchronizeImpl.exit

if.end.i39.i:                                     ; preds = %if.end20.i
  %35 = atomicrmw add ptr %arrayidx16.i, i32 -1 syncscope("device-one-as") monotonic, align 4
  br label %__mcImplicitDeviceSynchronizeImpl.exit

__mcImplicitDeviceSynchronizeImpl.exit:           ; preds = %if.then9.i, %if.then11.i, %if.then.i41.i, %if.end.i39.i
  ret void
}

; Function Attrs: mustprogress nofree norecurse nosync nounwind willreturn memory(argmem: write)
define protected metaxgpu_kernel void @attribution_elementwise(ptr addrspace(1) nocapture noundef writeonly %output.coerce) local_unnamed_addr #3 {
entry:
  %0 = tail call noundef range(i32 0, 1024) i32 @llvm.mxc.thread.id.x(), !range !25
  %1 = shl nuw nsw i32 %0, 1
  %2 = or disjoint i32 %1, 1
  %add = uitofp nneg i32 %2 to float
  %3 = tail call noundef range(i32 0, 2147483647) i32 @llvm.mxc.block.id.x(), !range !15
  %4 = tail call align 4 dereferenceable(64) ptr addrspace(4) @llvm.mxc.dispatch.ptr()
  %5 = getelementptr inbounds i8, ptr addrspace(4) %4, i64 4
  %6 = load i16, ptr addrspace(4) %5, align 4, !range !26, !invariant.load !14
  %conv4 = zext nneg i16 %6 to i32
  %mul5 = mul i32 %3, %conv4
  %add7 = add i32 %mul5, %0
  %idxprom = zext i32 %add7 to i64
  %arrayidx = getelementptr inbounds float, ptr addrspace(1) %output.coerce, i64 %idxprom
  store float %add, ptr addrspace(1) %arrayidx, align 4, !tbaa !27
  ret void
}

; Function Attrs: convergent mustprogress nofree norecurse nounwind willreturn memory(argmem: write)
define protected metaxgpu_kernel void @attribution_mma(ptr addrspace(1) nocapture noundef writeonly %output.coerce) local_unnamed_addr #4 {
entry:
  %0 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> zeroinitializer)
  %1 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %0)
  %2 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %1)
  %3 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %2)
  %4 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %3)
  %5 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %4)
  %6 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %5)
  %7 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %6)
  %8 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %7)
  %9 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %8)
  %10 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %9)
  %11 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %10)
  %12 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %11)
  %13 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %12)
  %14 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %13)
  %15 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %14)
  %16 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %15)
  %17 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %16)
  %18 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %17)
  %19 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %18)
  %20 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %19)
  %21 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %20)
  %22 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %21)
  %23 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %22)
  %24 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %23)
  %25 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %24)
  %26 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %25)
  %27 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %26)
  %28 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %27)
  %29 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %28)
  %30 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %29)
  %31 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %30)
  %32 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %31)
  %33 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %32)
  %34 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %33)
  %35 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %34)
  %36 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %35)
  %37 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %36)
  %38 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %37)
  %39 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %38)
  %40 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %39)
  %41 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %40)
  %42 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %41)
  %43 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %42)
  %44 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %43)
  %45 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %44)
  %46 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %45)
  %47 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %46)
  %48 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %47)
  %49 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %48)
  %50 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %49)
  %51 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %50)
  %52 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %51)
  %53 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %52)
  %54 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %53)
  %55 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %54)
  %56 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %55)
  %57 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %56)
  %58 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %57)
  %59 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %58)
  %60 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %59)
  %61 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %60)
  %62 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %61)
  %63 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %62)
  %64 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %63)
  %65 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %64)
  %66 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %65)
  %67 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %66)
  %68 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %67)
  %69 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %68)
  %70 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %69)
  %71 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %70)
  %72 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %71)
  %73 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %72)
  %74 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %73)
  %75 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %74)
  %76 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %75)
  %77 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %76)
  %78 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %77)
  %79 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %78)
  %80 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %79)
  %81 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %80)
  %82 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %81)
  %83 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %82)
  %84 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %83)
  %85 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %84)
  %86 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %85)
  %87 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %86)
  %88 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %87)
  %89 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %88)
  %90 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %89)
  %91 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %90)
  %92 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %91)
  %93 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %92)
  %94 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %93)
  %95 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %94)
  %96 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %95)
  %97 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %96)
  %98 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %97)
  %99 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %98)
  %100 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %99)
  %101 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %100)
  %102 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %101)
  %103 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %102)
  %104 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %103)
  %105 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %104)
  %106 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %105)
  %107 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %106)
  %108 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %107)
  %109 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %108)
  %110 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %109)
  %111 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %110)
  %112 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %111)
  %113 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %112)
  %114 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %113)
  %115 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %114)
  %116 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %115)
  %117 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %116)
  %118 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %117)
  %119 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %118)
  %120 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %119)
  %121 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %120)
  %122 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %121)
  %123 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %122)
  %124 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %123)
  %125 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %124)
  %126 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %125)
  %127 = tail call contract <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half> <half 0xH3C00, half 0xH3C00, half 0xH3C00, half 0xH3C00>, <4 x half> <half 0xH4000, half 0xH4000, half 0xH4000, half 0xH4000>, <4 x float> %126)
  %128 = tail call noundef range(i32 0, 2147483647) i32 @llvm.mxc.block.id.x(), !range !15
  %mul = shl i32 %128, 8
  %idx.ext = zext i32 %mul to i64
  %add.ptr = getelementptr inbounds float, ptr addrspace(1) %output.coerce, i64 %idx.ext
  %129 = tail call i32 @llvm.mxc.mbcnt.lo(i32 -1, i32 0) #8
  %130 = tail call noundef i32 @llvm.mxc.mbcnt.hi(i32 -1, i32 %129) #8
  %131 = tail call i32 @llvm.mxc.mbcnt.lo(i32 -1, i32 0) #8
  %132 = tail call noundef i32 @llvm.mxc.mbcnt.hi(i32 -1, i32 %131) #8
  %and.i = and i32 %132, 15
  %c.sroa.0.0.vec.extract = extractelement <4 x float> %127, i64 0
  %133 = shl i32 %130, 2
  %shl.i = and i32 %133, -64
  %add.i = or disjoint i32 %and.i, %shl.i
  %idxprom.i = zext i32 %add.i to i64
  %arrayidx.i = getelementptr inbounds float, ptr addrspace(1) %add.ptr, i64 %idxprom.i
  store float %c.sroa.0.0.vec.extract, ptr addrspace(1) %arrayidx.i, align 4, !tbaa !27
  %c.sroa.0.4.vec.extract = extractelement <4 x float> %127, i64 1
  %add6.i = or disjoint i32 %add.i, 16
  %idxprom7.i = zext i32 %add6.i to i64
  %arrayidx8.i = getelementptr inbounds float, ptr addrspace(1) %add.ptr, i64 %idxprom7.i
  store float %c.sroa.0.4.vec.extract, ptr addrspace(1) %arrayidx8.i, align 4, !tbaa !27
  %c.sroa.0.8.vec.extract = extractelement <4 x float> %127, i64 2
  %add13.i = or disjoint i32 %add.i, 32
  %idxprom14.i = zext i32 %add13.i to i64
  %arrayidx15.i = getelementptr inbounds float, ptr addrspace(1) %add.ptr, i64 %idxprom14.i
  store float %c.sroa.0.8.vec.extract, ptr addrspace(1) %arrayidx15.i, align 4, !tbaa !27
  %c.sroa.0.12.vec.extract = extractelement <4 x float> %127, i64 3
  %add20.i = or disjoint i32 %add.i, 48
  %idxprom21.i = zext i32 %add20.i to i64
  %arrayidx22.i = getelementptr inbounds float, ptr addrspace(1) %add.ptr, i64 %idxprom21.i
  store float %c.sroa.0.12.vec.extract, ptr addrspace(1) %arrayidx22.i, align 4, !tbaa !27
  ret void
}

; Function Attrs: mustprogress nofree nosync nounwind speculatable willreturn memory(none)
declare i32 @llvm.mxc.thread.id.x() #5

; Function Attrs: mustprogress nofree nosync nounwind speculatable willreturn memory(none)
declare i32 @llvm.mxc.block.id.x() #5

; Function Attrs: mustprogress nofree nosync nounwind speculatable willreturn memory(none)
declare align 4 ptr addrspace(4) @llvm.mxc.dispatch.ptr() #5

; Function Attrs: convergent mustprogress nofree nounwind willreturn memory(none)
declare i32 @llvm.mxc.mbcnt.lo(i32, i32) #6

; Function Attrs: convergent mustprogress nofree nounwind willreturn memory(none)
declare i32 @llvm.mxc.mbcnt.hi(i32, i32) #6

; Function Attrs: convergent mustprogress nofree nounwind willreturn memory(none)
declare <4 x float> @llvm.mxc.mma.f32.16x16x16f16(<4 x half>, <4 x half>, <4 x float>) #6

; Function Attrs: mustprogress nofree nosync nounwind speculatable willreturn memory(none)
declare align 4 ptr addrspace(4) @llvm.mxc.implicitarg.ptr() #5

; Function Attrs: mustprogress nofree nosync nounwind speculatable willreturn memory(none)
declare i32 @llvm.mxc.block.id.z() #5

; Function Attrs: mustprogress nofree nosync nounwind speculatable willreturn memory(none)
declare i32 @llvm.mxc.block.id.y() #5

; Function Attrs: mustprogress nofree nosync nounwind speculatable willreturn memory(none)
declare i1 @llvm.mxc.is.private(ptr nocapture) #5

; Function Attrs: mustprogress nounwind willreturn
declare void @llvm.mxc.sleep(i32 immarg) #7

attributes #0 = { mustprogress noreturn nounwind "disable-promote-alloca-to-bsm"="true" "disable-promote-alloca-to-vector"="false" "enable-ldg-bsm-opt"="false" "fixed-function-abi"="true" "metaxgpu-bsm-direct-address"="true" "metaxgpu-inline-scope"="11" "metaxgpu-max-block-size"="512" "metaxgpu-new-streg-abi"="false" "metaxgpu-pk-fma"="false" "metaxgpu-resource-usage"="false" "metaxgpu-sched-select"="default" "metaxgpu-use-dim-intrinsic"="false" "no-trapping-math"="true" "scalarize-global-loads"="true" "shfl-combine"="true" "stack-protector-buffer-size"="8" "target-cpu"="xcore1000" "target-features"="+xcore1000" }
attributes #1 = { cold noreturn nounwind memory(inaccessiblemem: write) }
attributes #2 = { mustprogress nounwind "disable-promote-alloca-to-bsm"="true" "disable-promote-alloca-to-vector"="false" "enable-ldg-bsm-opt"="false" "fixed-function-abi"="true" "metaxgpu-bsm-direct-address"="true" "metaxgpu-inline-scope"="11" "metaxgpu-max-block-size"="512" "metaxgpu-new-streg-abi"="false" "metaxgpu-pk-fma"="false" "metaxgpu-resource-usage"="false" "metaxgpu-sched-select"="default" "metaxgpu-use-dim-intrinsic"="false" "no-trapping-math"="true" "scalarize-global-loads"="true" "shfl-combine"="true" "stack-protector-buffer-size"="8" "target-cpu"="xcore1000" "target-features"="+xcore1000" }
attributes #3 = { mustprogress nofree norecurse nosync nounwind willreturn memory(argmem: write) "disable-promote-alloca-to-bsm"="true" "disable-promote-alloca-to-vector"="false" "enable-ldg-bsm-opt"="false" "fixed-function-abi"="true" "metaxgpu-bsm-direct-address"="true" "metaxgpu-implicitarg-num-bytes"="80" "metaxgpu-inline-scope"="11" "metaxgpu-max-block-size"="512" "metaxgpu-new-streg-abi"="false" "metaxgpu-pk-fma"="false" "metaxgpu-resource-usage"="false" "metaxgpu-sched-select"="default" "metaxgpu-use-dim-intrinsic"="false" "no-trapping-math"="true" "scalarize-global-loads"="true" "shfl-combine"="true" "stack-protector-buffer-size"="8" "target-cpu"="xcore1000" "target-features"="+xcore1000" "uniform-work-group-size"="true" }
attributes #4 = { convergent mustprogress nofree norecurse nounwind willreturn memory(argmem: write) "disable-promote-alloca-to-bsm"="true" "disable-promote-alloca-to-vector"="false" "enable-ldg-bsm-opt"="false" "fixed-function-abi"="true" "metaxgpu-bsm-direct-address"="true" "metaxgpu-implicitarg-num-bytes"="80" "metaxgpu-inline-scope"="11" "metaxgpu-max-block-size"="512" "metaxgpu-new-streg-abi"="false" "metaxgpu-pk-fma"="false" "metaxgpu-resource-usage"="false" "metaxgpu-sched-select"="default" "metaxgpu-use-dim-intrinsic"="false" "no-trapping-math"="true" "scalarize-global-loads"="true" "shfl-combine"="true" "stack-protector-buffer-size"="8" "target-cpu"="xcore1000" "target-features"="+xcore1000" "uniform-work-group-size"="true" }
attributes #5 = { mustprogress nofree nosync nounwind speculatable willreturn memory(none) }
attributes #6 = { convergent mustprogress nofree nounwind willreturn memory(none) }
attributes #7 = { mustprogress nounwind willreturn }
attributes #8 = { nomerge }

!llvm.module.flags = !{!0, !1}

!0 = !{i32 1, !"wchar_size", i32 4}
!1 = !{i32 8, !"PIC Level", i32 1}
!2 = !{!3, !3, i64 0}
!3 = !{!"long", !4, i64 0}
!4 = !{!"omnipotent char", !5, i64 0}
!5 = !{!"Simple C++ TBAA"}
!6 = !{!7, !9, i64 16}
!7 = !{!"_ZTS21_DynamicParallMqlWrap", !8, i64 0, !8, i64 4, !8, i64 8, !8, i64 12, !9, i64 16, !9, i64 24, !10, i64 32}
!8 = !{!"int", !4, i64 0}
!9 = !{!"any pointer", !4, i64 0}
!10 = !{!"_ZTS28mxc_kernel_dispatch_packet_s", !11, i64 0, !11, i64 2, !11, i64 4, !11, i64 6, !11, i64 8, !11, i64 10, !8, i64 12, !8, i64 16, !8, i64 20, !8, i64 24, !8, i64 28, !3, i64 32, !9, i64 40, !3, i64 48, !12, i64 56}
!11 = !{!"short", !4, i64 0}
!12 = !{!"_ZTS12mxc_signal_s", !3, i64 0}
!13 = !{i32 1, i32 -2147483648}
!14 = !{}
!15 = !{i32 0, i32 2147483647}
!16 = !{!8, !8, i64 0}
!17 = distinct !{!17, !18}
!18 = !{!"llvm.loop.mustprogress"}
!19 = !{!7, !8, i64 4}
!20 = !{!7, !9, i64 24}
!21 = !{!7, !8, i64 0}
!22 = !{!23, !9, i64 24}
!23 = !{!"_ZTS28_DynamicParallSchedulerParam", !3, i64 0, !9, i64 8, !9, i64 16, !9, i64 24, !3, i64 32, !3, i64 40, !3, i64 48, !3, i64 56, !3, i64 64, !3, i64 72, !3, i64 80, !3, i64 88, !3, i64 96, !8, i64 104, !8, i64 108, !8, i64 112, !8, i64 116, !8, i64 120, !8, i64 124}
!24 = !{!23, !3, i64 80}
!25 = !{i32 0, i32 1024}
!26 = !{i16 1, i16 1025}
!27 = !{!28, !28, i64 0}
!28 = !{!"float", !4, i64 0}
