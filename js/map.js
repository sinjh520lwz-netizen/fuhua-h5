let map = null;
let mapMarkers = [];
let mapType = 'standard';

function initMap() {
  try {
    map = new AMap.Map('mapContainer', {
      zoom: 9,
      center: [109.12, 21.48],
      mapStyle: 'amap://styles/normal',
      resizeEnable: true
    });
  } catch(e) {
    document.getElementById('mapContainer').innerHTML = '<div style="padding:40px;text-align:center;color:#999">地图加载失败，请刷新重试</div>';
  }
}

function renderMapMarkers(shipsWithPos) {
  if (!map) return;
  if (mapMarkers.length) { map.remove(mapMarkers); mapMarkers = []; }

  shipsWithPos.forEach(item => {
    const { ship, position: pos } = item;
    const status = Util.getShipStatus(pos);
    if (status.status === 'offline' && !pos) return;
    if (!pos || !pos.lon || !pos.lat) return;

    const lng = pos.lon / 1000000;
    const lat = pos.lat / 1000000;
    const hasWarn = Util.hasBanWarning(ship, pos);

    let color = '#95A5A6';
    if (status.status === 'sailing') color = hasWarn ? '#E74C3C' : '#27AE60';
    else if (status.status === 'moored') color = '#3498DB';
    if (ship.isSouthSea) color = '#2980B9';

    const marker = new AMap.Marker({
      position: [lng, lat],
      title: ship.shipName,
      label: {
        content: '<div style="background:' + color + ';color:#fff;padding:2px 6px;border-radius:4px;font-size:11px;white-space:nowrap">' + ship.shipName + '</div>',
        direction: 'top'
      },
      extData: { shipId: ship.shipId }
    });

    marker.on('click', () => showShipDetail(item));
    mapMarkers.push(marker);
  });

  map.add(mapMarkers);

  if (mapMarkers.length) {
    map.setFitView(mapMarkers, false, [60, 60, 60, 60]);
  }
}

function toggleMapType() {
  if (!map) return;
  mapType = mapType === 'standard' ? 'satellite' : 'standard';
  map.setMapStyle(mapType === 'satellite' ? 'amap://styles/satellite' : 'amap://styles/normal');
  document.getElementById('mapTypeBtn').textContent = mapType === 'standard' ? '🗺 卫星' : '🗺 标准';
}
