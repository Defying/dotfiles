// Reference : https://medium.com/@aghajari/liquid-glass-ios-effect-explanation-dabadd6414ae

uniform float glass_width_px;
uniform float glass_height_px;
uniform float glass_radius_px;
uniform float distortion_depth;
uniform float distortion_strength;
uniform float chromatic_shift_px;
uniform float glass_tint;

float sdf(vec2 p, vec2 b, float r) {
    vec2 d = abs(p) - b + vec2(r);
    return min(max(d.x, d.y), 0.0) + length(max(d, 0.0)) - r;
}

vec3 getTextureColorAt(vec2 coord, vec2 rect_size) {
    vec2 sample_uv = clamp(coord / max(rect_size, vec2(1.0)), vec2(0.0), vec2(1.0));
    return texture2D(tex, sample_uv).rgb;
}

vec4 shader_main(vec2 uv, vec2 rect_size) {
    vec2 fragCoord = uv * rect_size;
    vec2 glassSize = vec2(
        glass_width_px > 0.0 ? glass_width_px : rect_size.x,
        glass_height_px > 0.0 ? glass_height_px : rect_size.y
    );
    vec2 glassCenter = rect_size * 0.5;
    vec2 glassCoord = fragCoord - glassCenter;

    float size = max(min(glassSize.x, glassSize.y), 1.0);
    float inversedSDF = -sdf(glassCoord, glassSize * 0.5, glass_radius_px) / size;

    if (inversedSDF < 0.0) {
        return vec4(getTextureColorAt(fragCoord, rect_size), 1.0);
    }

    float coordLen = length(glassCoord);
    vec2 normalizedGlassCoord = coordLen > 0.0001
        ? glassCoord / coordLen
        : vec2(0.0, 0.0);
    float distFromCenter = 1.0 - clamp(inversedSDF / max(distortion_depth, 0.0001), 0.0, 1.0);
    float distortion = 1.0 - sqrt(max(1.0 - pow(distFromCenter, 2.0), 0.0));
    vec2 offset = distortion * normalizedGlassCoord * glassSize * 0.5 * distortion_strength;
    vec2 glassColorCoord = fragCoord - offset;

    float edge = smoothstep(0.0, 0.02, inversedSDF);
    vec2 shift = normalizedGlassCoord * edge * chromatic_shift_px;
    vec3 glassColor = vec3(
        getTextureColorAt(glassColorCoord - shift, rect_size).r,
        getTextureColorAt(glassColorCoord, rect_size).g,
        getTextureColorAt(glassColorCoord + shift, rect_size).b
    );

    glassColor *= vec3(glass_tint);
    return vec4(glassColor, 1.0);
}
