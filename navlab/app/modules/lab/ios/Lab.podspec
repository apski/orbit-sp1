Pod::Spec.new do |s|
  s.name           = 'Lab'
  s.version        = '1.0.0'
  s.summary        = 'signposts for the nav lab'
  s.author         = 'lab'
  s.homepage       = 'https://example.invalid'
  s.license        = 'MIT'
  s.platforms      = { :ios => '16.4' }
  s.swift_version  = '5.9'
  s.source         = { git: '' }
  s.static_framework = true
  s.dependency 'ExpoModulesCore'
  s.source_files = "**/*.{h,m,swift}"
  s.pod_target_xcconfig = { 'DEFINES_MODULE' => 'YES' }
end
