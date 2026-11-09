# Paquete local: ejecuta `makepkg -si` dentro de esta carpeta.
pkgname=cpu-control
pkgver=0.3.0
pkgrel=1
pkgdesc="Monitor y ajuste de procesadores AMD Ryzen al estilo Ryzen Master (GTK4, Wayland)"
arch=('any')
license=('GPL-3.0-or-later')
depends=('python' 'python-gobject' 'python-cairo' 'gtk4' 'libadwaita' 'librsvg' 'polkit')
optdepends=(
  'ryzen_smu-dkms-git: PBO, Curve Optimizer, OC manual y tabla PM (AUR, muy recomendado)'
  'zenpower3-dkms: voltaje, corriente y potencia SVI2 en Zen 1-3 (AUR)'
  'stress-ng: pruebas de estabilidad del Asistente OC'
  'polkit-gnome: agente de autenticación para Sway y otros compositores sin agente'
  'ttf-jetbrains-mono: tipografía de los valores numéricos'
)
install=cpu-control.install

package() {
  cd "$startdir"
  make DESTDIR="$pkgdir" PREFIX=/usr install
}
