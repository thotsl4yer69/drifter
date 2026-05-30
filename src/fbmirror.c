/*
 * MZ1312 DRIFTER — Framebuffer Mirror
 * Copies fb0 (HDMI) → fb1 (SPI LCD) with nearest-neighbor scaling.
 * Works on Pi 5 (no VideoCore dependency).
 *
 * Build: gcc -O2 -o fbmirror fbmirror.c
 * Usage: fbmirror [fps]  (default: 25)
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <time.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <linux/fb.h>

static int open_fb(const char *path, struct fb_var_screeninfo *vinfo,
                   struct fb_fix_screeninfo *finfo) {
    int fd = open(path, O_RDWR);
    if (fd < 0) { perror(path); return -1; }
    if (ioctl(fd, FBIOGET_VSCREENINFO, vinfo) < 0) { perror("VSCREENINFO"); close(fd); return -1; }
    if (ioctl(fd, FBIOGET_FSCREENINFO, finfo) < 0) { perror("FSCREENINFO"); close(fd); return -1; }
    return fd;
}

int main(int argc, char *argv[]) {
    int fps = argc > 1 ? atoi(argv[1]) : 25;
    if (fps < 1) fps = 1;
    if (fps > 60) fps = 60;

    struct fb_var_screeninfo src_v, dst_v;
    struct fb_fix_screeninfo src_f, dst_f;

    int src_fd = open_fb("/dev/fb0", &src_v, &src_f);
    if (src_fd < 0) return 1;

    /* Wait for fb1 to appear (overlay may be slow) */
    int dst_fd = -1;
    for (int i = 0; i < 30; i++) {
        dst_fd = open_fb("/dev/fb1", &dst_v, &dst_f);
        if (dst_fd >= 0) break;
        fprintf(stderr, "fbmirror: waiting for /dev/fb1...\n");
        sleep(2);
    }
    if (dst_fd < 0) {
        fprintf(stderr, "fbmirror: /dev/fb1 not found\n");
        return 1;
    }

    int sw = src_v.xres, sh = src_v.yres, sbpp = src_v.bits_per_pixel;
    int dw = dst_v.xres, dh = dst_v.yres, dbpp = dst_v.bits_per_pixel;

    fprintf(stderr, "fbmirror: fb0 %dx%d@%d → fb1 %dx%d@%d @ %dfps\n",
            sw, sh, sbpp, dw, dh, dbpp, fps);

    size_t src_size = src_f.line_length * sh;
    size_t dst_size = dst_f.line_length * dh;

    unsigned char *src = mmap(NULL, src_size, PROT_READ, MAP_SHARED, src_fd, 0);
    unsigned char *dst = mmap(NULL, dst_size, PROT_READ | PROT_WRITE, MAP_SHARED, dst_fd, 0);

    if (src == MAP_FAILED || dst == MAP_FAILED) {
        perror("mmap");
        return 1;
    }

    int src_bpp = sbpp / 8;
    int dst_bpp = dbpp / 8;
    int bpp = src_bpp < dst_bpp ? src_bpp : dst_bpp; /* min for copy */

    /* Precompute x/y lookup tables */
    int *xmap = malloc(dw * sizeof(int));
    int *ymap = malloc(dh * sizeof(int));
    for (int x = 0; x < dw; x++) xmap[x] = (x * sw / dw);
    for (int y = 0; y < dh; y++) ymap[y] = (y * sh / dh);

    long ns_per_frame = 1000000000L / fps;
    struct timespec t0, t1;

    fprintf(stderr, "fbmirror: running\n");

    for (;;) {
        clock_gettime(CLOCK_MONOTONIC, &t0);

        for (int dy = 0; dy < dh; dy++) {
            int sy = ymap[dy];
            unsigned char *src_row = src + sy * src_f.line_length;
            unsigned char *dst_row = dst + dy * dst_f.line_length;

            if (sw == dw && src_bpp == dst_bpp) {
                /* Same width — direct copy */
                memcpy(dst_row, src_row, dw * bpp);
            } else {
                /* Scaled copy */
                for (int dx = 0; dx < dw; dx++) {
                    int sx = xmap[dx];
                    memcpy(dst_row + dx * dst_bpp,
                           src_row + sx * src_bpp,
                           bpp);
                }
            }
        }

        clock_gettime(CLOCK_MONOTONIC, &t1);
        long elapsed = (t1.tv_sec - t0.tv_sec) * 1000000000L + (t1.tv_nsec - t0.tv_nsec);
        long remaining = ns_per_frame - elapsed;
        if (remaining > 0) {
            struct timespec sl = { .tv_sec = 0, .tv_nsec = remaining };
            nanosleep(&sl, NULL);
        }
    }

    munmap(src, src_size);
    munmap(dst, dst_size);
    free(xmap);
    free(ymap);
    close(src_fd);
    close(dst_fd);
    return 0;
}
