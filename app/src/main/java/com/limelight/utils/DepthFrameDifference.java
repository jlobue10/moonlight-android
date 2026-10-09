package com.limelight.utils;

import org.opencv.core.Core;
import org.opencv.core.CvType;
import org.opencv.core.Mat;
import org.opencv.core.MatOfFloat;
import org.opencv.core.MatOfInt;
import org.opencv.imgproc.Imgproc;

import java.nio.ByteBuffer;
import java.util.Collections;

/** Reusable OpenCV scratch data owned exclusively by one depth-result worker. */
final class DepthFrameDifference implements AutoCloseable {
    private final int width;
    private final int height;
    private final Mat gray1 = new Mat(), gray2 = new Mat();
    private final Mat edges1 = new Mat(), edges2 = new Mat();
    // Keep signed gradients separate from their 8-bit magnitudes. Reusing one Mat
    // for both depths would reallocate its image storage on every Sobel pass.
    private final Mat gradX = new Mat(), gradY = new Mat();
    private final Mat absX = new Mat(), absY = new Mat();
    private final Mat histGray1 = new Mat(), histGray2 = new Mat();
    private final Mat histEdge1 = new Mat(), histEdge2 = new Mat();
    private final Mat emptyMask = new Mat();
    private final MatOfInt channels = new MatOfInt(0);
    private final MatOfInt bins = new MatOfInt(256);
    private final MatOfFloat range = new MatOfFloat(0f, 256f);

    DepthFrameDifference(int width, int height) {
        this.width = width;
        this.height = height;
    }

    double compare(ByteBuffer current, ByteBuffer previous) {
        if (current == null || previous == null || current.capacity() != previous.capacity()) {
            return 1.0;
        }
        Mat rgba1 = null, rgba2 = null;
        try {
            // These two headers view the caller-owned buffers without copying pixels.
            rgba1 = new Mat(height, width, CvType.CV_8UC4, current);
            rgba2 = new Mat(height, width, CvType.CV_8UC4, previous);
            Imgproc.cvtColor(rgba1, gray1, Imgproc.COLOR_RGBA2GRAY);
            Imgproc.cvtColor(rgba2, gray2, Imgproc.COLOR_RGBA2GRAY);
            edges(gray1, edges1);
            edges(gray2, edges2);
            histogram(gray1, histGray1);
            histogram(gray2, histGray2);
            histogram(edges1, histEdge1);
            histogram(edges2, histEdge2);
            double grayDiff = 1.0 - Imgproc.compareHist(histGray1, histGray2, Imgproc.HISTCMP_CORREL);
            double edgeDiff = 1.0 - Imgproc.compareHist(histEdge1, histEdge2, Imgproc.HISTCMP_CORREL);
            return 0.5 * grayDiff + 0.5 * edgeDiff;
        } finally {
            if (rgba1 != null) rgba1.release();
            if (rgba2 != null) rgba2.release();
        }
    }

    private void edges(Mat gray, Mat output) {
        Imgproc.Sobel(gray, gradX, CvType.CV_16S, 1, 0);
        Imgproc.Sobel(gray, gradY, CvType.CV_16S, 0, 1);
        Core.convertScaleAbs(gradX, absX);
        Core.convertScaleAbs(gradY, absY);
        Core.addWeighted(absX, 0.5, absY, 0.5, 0, output);
    }

    private void histogram(Mat image, Mat output) {
        Imgproc.calcHist(Collections.singletonList(image), channels, emptyMask, output, bins, range);
    }

    @Override
    public void close() {
        gray1.release(); gray2.release();
        edges1.release(); edges2.release();
        gradX.release(); gradY.release();
        absX.release(); absY.release();
        histGray1.release(); histGray2.release();
        histEdge1.release(); histEdge2.release();
        emptyMask.release(); channels.release(); bins.release(); range.release();
    }
}
