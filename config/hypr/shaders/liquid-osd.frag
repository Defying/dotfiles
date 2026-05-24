#version 300 es
precision highp float;

in vec2 v_texcoord;
out vec4 fragColor;
uniform sampler2D tex;

const vec2 SCREEN = vec2(2560.0, 1600.0);
const float SCREEN_RADIUS = 28.0;

const vec2 OSD_CENTER = vec2(1280.0, 252.167);
const vec2 OSD_SIZE = vec2(533.333, 120.0);
const float OSD_RADIUS = 36.667;

const float DISTORTION_DEPTH = 0.22;
const float DISTORTION_STRENGTH = 0.16;
const float CHROMATIC_SHIFT_PX = 3.0;
const float GLASS_TINT = 0.94;
const float EDGE_HIGHLIGHT = 0.20;
const float BLUR_PX = 2.25;
const float FROST_AMOUNT = 0.28;
const float FROST_VEIL = 0.16;

float roundedSdf(vec2 p, vec2 halfSize, float radius) {
    vec2 d = abs(p) - halfSize + vec2(radius);
    return min(max(d.x, d.y), 0.0) + length(max(d, 0.0)) - radius;
}

vec3 sampleScreen(vec2 coord) {
    vec2 uv = clamp(coord / SCREEN, vec2(0.0), vec2(1.0));
    return texture(tex, uv).rgb;
}

vec3 liquidGlass(vec2 pix, vec3 baseColor) {
    vec2 glassCoord = pix - OSD_CENTER;
    vec2 halfSize = OSD_SIZE * 0.5;

    if (abs(glassCoord.x) > halfSize.x + 2.0 || abs(glassCoord.y) > halfSize.y + 2.0) {
        return baseColor;
    }

    float size = max(min(OSD_SIZE.x, OSD_SIZE.y), 1.0);
    float inside = -roundedSdf(glassCoord, halfSize, OSD_RADIUS) / size;
    float mask = smoothstep(-0.006, 0.006, inside);
    if (mask <= 0.0) {
        return baseColor;
    }

    float coordLen = length(glassCoord);
    vec2 normal = coordLen > 0.0001 ? glassCoord / coordLen : vec2(0.0);
    float distFromCenter = 1.0 - clamp(inside / DISTORTION_DEPTH, 0.0, 1.0);
    float distortion = 1.0 - sqrt(max(1.0 - distFromCenter * distFromCenter, 0.0));
    vec2 offset = distortion * normal * OSD_SIZE * 0.5 * DISTORTION_STRENGTH;
    vec2 coord = pix - offset;

    float rim = 1.0 - smoothstep(0.0, 0.035, inside);
    vec2 shift = normal * rim * CHROMATIC_SHIFT_PX;
    vec3 refracted = vec3(
        sampleScreen(coord - shift).r,
        sampleScreen(coord).g,
        sampleScreen(coord + shift).b
    );

    vec3 blurred = refracted * 0.40;
    blurred += sampleScreen(coord + vec2(BLUR_PX, 0.0)) * 0.11;
    blurred += sampleScreen(coord - vec2(BLUR_PX, 0.0)) * 0.11;
    blurred += sampleScreen(coord + vec2(0.0, BLUR_PX)) * 0.11;
    blurred += sampleScreen(coord - vec2(0.0, BLUR_PX)) * 0.11;
    blurred += sampleScreen(coord + vec2(BLUR_PX, BLUR_PX)) * 0.08;
    blurred += sampleScreen(coord + vec2(-BLUR_PX, BLUR_PX)) * 0.08;

    float topLight = 1.0 - smoothstep(-halfSize.y, -halfSize.y * 0.15, glassCoord.y);
    float diagonal = 1.0 - smoothstep(-0.65, 0.35, glassCoord.x / halfSize.x + glassCoord.y / halfSize.y);
    float highlight = clamp(rim * EDGE_HIGHLIGHT + topLight * diagonal * 0.07, 0.0, 0.28);

    float luma = dot(blurred, vec3(0.299, 0.587, 0.114));
    vec3 frosted = mix(blurred, vec3(luma), FROST_AMOUNT);
    frosted = mix(frosted, vec3(1.0), FROST_VEIL);

    vec3 glassColor = mix(frosted, vec3(1.0), highlight);
    glassColor *= vec3(GLASS_TINT);
    glassColor = mix(glassColor, vec3(0.74, 0.52, 0.95), 0.035);

    return mix(baseColor, glassColor, mask);
}

vec4 roundedScreenCorners(vec2 pix, vec4 color) {
    vec2 corner = min(pix, SCREEN - pix);

    if (corner.x < SCREEN_RADIUS && corner.y < SCREEN_RADIUS) {
        vec2 d = vec2(SCREEN_RADIUS) - corner;
        float dist = length(d);
        float aa = smoothstep(SCREEN_RADIUS - 1.0, SCREEN_RADIUS + 0.5, dist);
        color.rgb = mix(color.rgb, vec3(0.0), aa);
        color.a = mix(color.a, 1.0, aa);
    }

    return color;
}

void main() {
    vec2 pix = v_texcoord * SCREEN;
    vec4 color = texture(tex, v_texcoord);
    color.rgb = liquidGlass(pix, color.rgb);
    fragColor = roundedScreenCorners(pix, color);
}
