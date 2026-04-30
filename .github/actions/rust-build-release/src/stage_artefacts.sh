#!/usr/bin/env bash
set -euo pipefail

target="${1:?target argument is required}"
bin_name="${2:?binary name argument is required}"

case "${target}" in
  x86_64-unknown-linux-*)
    os=linux
    arch=amd64
    ;;
  x86_64-unknown-illumos)
    os=illumos
    arch=amd64
    ;;
  aarch64-unknown-linux-*)
    os=linux
    arch=arm64
    ;;
  i686-unknown-linux-*)
    os=linux
    arch=i386
    ;;
  armv7-unknown-linux-gnueabihf|arm-unknown-linux-gnueabihf|armv7-unknown-linux-musleabihf|arm-unknown-linux-musleabihf)
    os=linux
    arch=armhf
    ;;
  riscv64*-unknown-linux-*)
    os=linux
    arch=riscv64
    ;;
  powerpc64le-unknown-linux-*|ppc64le-unknown-linux-*)
    os=linux
    arch=ppc64el
    ;;
  *)
    echo "::error:: unsupported target ${target}"
    exit 1
    ;;
esac

stage_dir="dist/${bin_name}_${os}_${arch}"
mkdir -p "${stage_dir}"

bin_src="target/${target}/release/${bin_name}"
if [[ ! -f "${bin_src}" ]]; then
  echo "::error:: binary not found at ${bin_src}"
  exit 1
fi

man_path="target/generated-man/${target}/release/${bin_name}.1"
if [[ ! -f "${man_path}" ]]; then
  echo "::error:: man page not found at ${man_path}"
  exit 1
fi

if [[ ! -f "${man_path}" ]]; then
  echo "::error:: located man page ${man_path} is not a file"
  exit 1
fi

install -m 0755 "${bin_src}" "${stage_dir}/${bin_name}"
install -m 0644 "${man_path}" "${stage_dir}/${bin_name}.1"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  echo "man-path=${stage_dir}/${bin_name}.1" >> "${GITHUB_OUTPUT}"
fi
