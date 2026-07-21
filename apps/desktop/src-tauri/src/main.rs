//! STEPWORK Desktop 入口。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    stepwork_desktop_lib::run()
}
