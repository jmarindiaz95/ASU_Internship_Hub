
var map = L.map('map').setView([33.4178, -111.9344], 12);

L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors',
    referrerPolicy: 'strict-origin-when-cross-origin'
}).addTo(map);

var campusIcon = L.divIcon({
    html: '★',
    className: 'campus-icon',
    iconSize: [36, 36],
    iconAnchor: [18, 18],
    popupAnchor: [0, -18]
});

var internshipIcon = L.divIcon({
    html: '<div class="internship-dot"></div>',
    className: 'internship-icon',
    iconSize: [24, 24],
    iconAnchor: [12, 12],
    popupAnchor: [0, -12]
});

L.marker([33.4178, -111.9344], {
    icon: campusIcon
}).addTo(map)
    .bindPopup('ASU Tempe Campus');

var studyArea = L.circle([33.4178, -111.9344], {
    radius: 30 * 1609.344,
    color: 'blue',
    fillOpacity: 0.05
}).addTo(map);

var internshipLocations = L.layerGroup().addTo(map);

var sampleInternship1 = L.marker([33.4300, -111.9400], {
    icon: internshipIcon
}).addTo(internshipLocations)
    .bindPopup('Sample Internship 1 - Test only');

var sampleInternship2 = L.marker([33.4500, -111.9200], {
    icon: internshipIcon
}).addTo(internshipLocations)
    .bindPopup('Sample Internship 2 - Test only');

var distance1 = map.distance(
    [33.4178, -111.9344],
    [33.4300, -111.9400]
);

var distance2 = map.distance(
    [33.4178, -111.9344],
    [33.4500, -111.9200]
);

L.control.layers(null, {
    'Study Area (up to 30 miles)': studyArea,
    'Internship Locations': internshipLocations
}).addTo(map);

document.getElementById('distance').addEventListener('input', function() {

    var miles = document.getElementById('distance').value;
    var meters = miles * 1609.344;

    studyArea.setRadius(meters);

    document.getElementById('distanceValue').textContent = miles + ' mi';

    if (distance1 <= meters) {
        internshipLocations.addLayer(sampleInternship1);
    } else {
        internshipLocations.removeLayer(sampleInternship1);
    }

    if (distance2 <= meters) {
        internshipLocations.addLayer(sampleInternship2);
    } else {
        internshipLocations.removeLayer(sampleInternship2);
    }

});

document.getElementById('resetMap').addEventListener('click', function() {
    map.setView([33.4178, -111.9344], 12);
});

document.getElementById('viewArea').addEventListener('click', function() {
    map.fitBounds(studyArea.getBounds());
});
