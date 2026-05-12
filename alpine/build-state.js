#!/usr/bin/env node

import path from "node:path";
import fs from "node:fs";
import url from "node:url";
import { V86 } from "../vendor/libv86.mjs";

const __dirname = url.fileURLToPath(new URL(".", import.meta.url));

const V86_ROOT = path.join(__dirname, "../");
const OUTPUT_FILE = path.join(V86_ROOT, "dist/alpine-state.bin");

var emulator = new V86({
    wasm_path: path.join(V86_ROOT, "vendor/v86.wasm"),
    bios: { url: path.join(V86_ROOT, "vendor/seabios.bin") },
    vga_bios: { url: path.join(V86_ROOT, "vendor/vgabios.bin") },
    autostart: true,
    memory_size: 512 * 1024 * 1024,
    vga_memory_size: 8 * 1024 * 1024,
    bzimage_initrd_from_filesystem: true,
    cmdline: "rw root=host9p rootfstype=9p rootflags=trans=virtio,cache=loose modules=virtio_pci tsc=reliable init_on_free=on",
    net_device: {
       type: "virtio",
       relay_url: "inbrowser",
       mtu: 1500,
    },
    filesystem: {
        baseurl: path.join(V86_ROOT, "dist/alpine-rootfs-flat"),
        basefs: path.join(V86_ROOT, "dist/alpine-fs.json"),
    },
});

console.log("Now booting, please stand by ...");

let serial_text = "";
let booted = false;

emulator.add_listener("serial0-output-byte", function(byte)
{
    const c = String.fromCharCode(byte);
    process.stdout.write(c);

    serial_text += c;

    if(!booted && serial_text.endsWith("localhost:~# "))
    {
        booted = true;

        emulator.serial0_send("sync;echo 3 >/proc/sys/vm/drop_caches\n");

        setTimeout(async function ()
            {
                const s = await emulator.save_state();

                fs.writeFile(OUTPUT_FILE, new Uint8Array(s), function(e)
                    {
                        if(e) throw e;
                        console.log("Saved as " + OUTPUT_FILE);
                        emulator.destroy();
                    });
            }, 10 * 1000);
    }
});
