package com.limelight.utils;

import org.junit.Test;
import org.tensorflow.lite.Interpreter;
import org.opencv.core.Core;
import org.opencv.core.Mat;
import org.opencv.imgproc.Imgproc;
import org.mockito.MockedConstruction;
import org.mockito.MockedStatic;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.nio.ByteBuffer;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.mockConstruction;
import static org.mockito.Mockito.mockStatic;

/** Executes the real worker/teardown without GL, OpenCV, JNI or a model file. */
public class StereoSessionTest {
    private static Object allocate(Class<?> type) throws Exception {
        // Bypass constructors only at native/platform boundaries. The real session,
        // worker and teardown methods below still run unchanged.
        Class<?> unsafeClass = Class.forName("sun.misc.Unsafe");
        Field field = unsafeClass.getDeclaredField("theUnsafe");
        field.setAccessible(true);
        return unsafeClass.getMethod("allocateInstance", Class.class).invoke(field.get(null), type);
    }

    private static class SlowInterpreter {
        final CountDownLatch entered = new CountDownLatch(1), finish = new CountDownLatch(1);
        final Interpreter nativeModel = mock(Interpreter.class);
        volatile int closes;
        SlowInterpreter() {
            doAnswer(call -> {
                entered.countDown();
                awaitNative(finish);
                ((ByteBuffer) call.getArgument(1)).put(0, (byte) 7);
                return null;
            }).when(nativeModel).run(any(), any());
            doAnswer(call -> { closes++; return null; }).when(nativeModel).close();
        }
    }

    private static SlowInterpreter model() throws Exception {
        return new SlowInterpreter();
    }

    private static void awaitNative(CountDownLatch latch) {
        boolean interrupted = false;
        while (true) {
            try { latch.await(); break; }
            catch (InterruptedException ignored) { interrupted = true; }
        }
        if (interrupted) Thread.currentThread().interrupt();
    }

    private static Field field(Object target, String name) throws Exception {
        Field field = target.getClass().getDeclaredField(name);
        field.setAccessible(true);
        return field;
    }
    private static Object get(Object target, String name) throws Exception { return field(target, name).get(target); }
    private static void set(Object target, String name, Object value) throws Exception { field(target, name).set(target, value); }
    private static Class<?> nested(Class<?> parent, String name) {
        for (Class<?> type : parent.getDeclaredClasses()) if (type.getSimpleName().equals(name)) return type;
        return null;
    }
    private static Object construct(Class<?> type, Object outer) throws Exception {
        Constructor<?> constructor = type.getDeclaredConstructor(outer.getClass());
        constructor.setAccessible(true);
        return constructor.newInstance(outer);
    }
    private static void check(boolean value, String message) {
        if (!value) throw new AssertionError(message);
    }

    private static Stereo3DRenderer renderer() throws Exception {
        Stereo3DRenderer renderer = (Stereo3DRenderer) allocate(Stereo3DRenderer.class);
        set(renderer, "depthModel", DepthModel.DEPTH_ANYTHING_V2_SMALL_252);
        set(renderer, "host", new Stereo3DRenderer.RenderHost() {
            public void requestRender() {}
            public void setContinuousRendering(boolean value) {}
            public void queueEvent(Runnable event) {} // GL resource release is not under test.
            public int getWidth() { return 2; }
            public int getHeight() { return 1; }
        });
        return renderer;
    }

    private static Object prepare(Stereo3DRenderer renderer, SlowInterpreter model) throws Exception {
        Class<?> sessionType = nested(Stereo3DRenderer.class, "DepthSession");
        Object owner = sessionType == null ? renderer : construct(sessionType, renderer);
        if (sessionType != null) set(renderer, "depthSession", owner);
        set(renderer, "stopped", false);
        set(owner, "modelInputWidth", 1);
        set(owner, "modelInputHeight", 1);
        set(owner, "floatInput", true);
        set(owner, "floatOutput", false);
        set(owner, "tflite", model == null ? null : model.nativeModel);
        set(owner, "tfliteInputBuffer", ByteBuffer.allocateDirect(12));
        set(owner, "isAiRunning", new AtomicBoolean(true));
        set(owner, "isAiResultHandlingRunning", new AtomicBoolean());
        set(owner, "latestDepthMap", new AtomicReference<ByteBuffer>());
        for (String queue : new String[]{"inferenceInputQueue", "freeInputBuffers", "freeOutputBuffers", "filledOutputBuffers", "freeSmoothedBuffers"}) {
            set(owner, queue, new ArrayBlockingQueue<>(10));
        }
        if (get(owner, "inferenceExecutor") == null) set(owner, "inferenceExecutor", Executors.newSingleThreadExecutor());
        queue(owner, "freeOutputBuffers").add(ByteBuffer.allocateDirect(1));
        return owner;
    }

    @SuppressWarnings("unchecked")
    private static BlockingQueue<Object> queue(Object owner, String name) throws Exception {
        return (BlockingQueue<Object>) get(owner, name);
    }
    private static ExecutorService executor(Object owner) throws Exception {
        return (ExecutorService) get(owner, "inferenceExecutor");
    }

    @Test public void oldInferenceCannotReturnBuffersOrClearRunningStateOfReplacement() throws Exception {
        Stereo3DRenderer renderer = renderer();
        SlowInterpreter oldModel = model(), replacementModel = model();
        Object oldOwner = prepare(renderer, oldModel);
        ExecutorService oldExecutor = executor(oldOwner);
        try {
            Class<?> result = nested(Stereo3DRenderer.class, "RenderResult");
            Constructor<?> resultConstructor = result.getDeclaredConstructor(ByteBuffer.class, double.class);
            resultConstructor.setAccessible(true);
            queue(oldOwner, "inferenceInputQueue").add(resultConstructor.newInstance(ByteBuffer.allocateDirect(4), 10.0));
            Runnable worker = (Runnable) construct(nested(oldOwner.getClass(), "AiTask"), oldOwner);
            Future<?> task = oldExecutor.submit(() -> {
                // Pixel conversion is a native boundary; keep the production
                // worker and its queue/buffer ownership operations intact.
                try (MockedConstruction<Mat> mats = mockConstruction(Mat.class);
                     MockedStatic<Imgproc> imgproc = mockStatic(Imgproc.class);
                     MockedStatic<Core> core = mockStatic(Core.class)) {
                    worker.run();
                }
            });
            set(oldOwner, "inferenceTask", task);
            if (!oldModel.entered.await(10, TimeUnit.SECONDS)) {
                if (task.isDone()) task.get();
                throw new AssertionError("old inference did not start");
            }
            renderer.onSurfaceDestroyed();
            Object replacement = prepare(renderer, replacementModel);
            oldModel.finish.countDown();
            check(oldExecutor.awaitTermination(3, TimeUnit.SECONDS), "old inference did not terminate");
            check(queue(replacement, "freeInputBuffers").isEmpty()
                    && queue(replacement, "freeOutputBuffers").size() == 1,
                    "old inference returned its buffers into replacement pools");
            check(((AtomicBoolean) get(replacement, "isAiRunning")).get(), "old worker cleared replacement running flag");
            check(oldModel.closes == 1 && replacementModel.closes == 0, "deferred close used the wrong model");
        } finally {
            oldModel.finish.countDown();
            renderer.onSurfaceDestroyed();
            oldExecutor.shutdownNow();
        }
    }

    @Test public void modelFinishingInitializationAfterStopIsClosedWithoutOverwritingReplacement() throws Exception {
        Stereo3DRenderer renderer = renderer();
        SlowInterpreter oldModel = model(), replacementModel = model();
        Object oldOwner = prepare(renderer, null);
        ExecutorService oldExecutor = executor(oldOwner);
        CountDownLatch entered = new CountDownLatch(1), finish = new CountDownLatch(1);
        try {
            oldExecutor.submit(() -> {
                entered.countDown(); awaitNative(finish);
                try { set(oldOwner, "tflite", oldModel.nativeModel); }
                catch (Exception e) { throw new AssertionError(e); }
            });
            check(entered.await(3, TimeUnit.SECONDS), "initialization did not start");
            renderer.onSurfaceDestroyed();
            Object replacement = prepare(renderer, replacementModel);
            finish.countDown();
            check(oldExecutor.awaitTermination(3, TimeUnit.SECONDS), "initialization cleanup did not finish");
            check(oldModel.closes == 1, "model created after stop was not closed");
            check(get(replacement, "tflite") == replacementModel.nativeModel && replacementModel.closes == 0,
                    "late initialization replaced or closed the new model");
        } finally {
            finish.countDown();
            renderer.onSurfaceDestroyed();
            oldExecutor.shutdownNow();
        }
    }

    public static void main(String[] args) throws Exception {
        StereoSessionTest test = new StereoSessionTest();
        if (args.length == 0 || args[0].equals("inference")) {
            test.oldInferenceCannotReturnBuffersOrClearRunningStateOfReplacement();
            System.out.println("PASS old inference is isolated from replacement pools and state");
        }
        if (args.length == 0 || args[0].equals("initialization")) {
            test.modelFinishingInitializationAfterStopIsClosedWithoutOverwritingReplacement();
            System.out.println("PASS late native initialization is owned and closed by its retired session");
        }
    }
}
