#!/usr/bin/env ruby
# One-off generator for the disposable sample app used to validate Flow Review
# end-to-end. Not part of the shipped tool.
require 'xcodeproj'
require 'fileutils'

root = File.expand_path(File.join(__dir__, 'SampleFlowApp'))
proj_path = File.join(root, 'SampleFlowApp.xcodeproj')
FileUtils.mkdir_p(File.join(root, 'SampleFlowApp'))

project = Xcodeproj::Project.new(proj_path)

app_target = project.new_target(:application, 'SampleFlowApp', :ios, '17.0', project.products_group, :swift, 'SampleFlowApp')

group = project.main_group.new_group('SampleFlowApp', 'SampleFlowApp')
group.set_source_tree('<group>')

%w[SampleFlowApp.swift ContentView.swift ItemStore.swift AddItemView.swift].each do |fname|
  ref = group.new_reference(fname)
  app_target.source_build_phase.add_file_reference(ref)
end

app_target.build_configuration_list.build_configurations.each do |config|
  config.build_settings['GENERATE_INFOPLIST_FILE'] = 'YES'
  config.build_settings['PRODUCT_BUNDLE_IDENTIFIER'] = 'com.flowreview.sampleflowapp'
  config.build_settings['SWIFT_VERSION'] = '5.0'
  config.build_settings['CODE_SIGN_STYLE'] = 'Automatic'
  config.build_settings['ENABLE_PREVIEWS'] = 'YES'
  config.build_settings['TARGETED_DEVICE_FAMILY'] = '1,2'
  config.build_settings['INFOPLIST_KEY_UILaunchScreen_Generation'] = 'YES'
  config.build_settings['INFOPLIST_KEY_UIApplicationSceneManifest_Generation'] = 'YES'
  config.build_settings['INFOPLIST_KEY_UISupportedInterfaceOrientations'] = 'UIInterfaceOrientationPortrait'
end

project.save

# Shared scheme so `xcodebuild -list` / CI can see it without opening Xcode.
scheme = Xcodeproj::XCScheme.new
scheme.add_build_target(app_target)
scheme.set_launch_target(app_target)
FileUtils.mkdir_p(File.join(proj_path, 'xcshareddata', 'xcschemes'))
scheme.save_as(proj_path, 'SampleFlowApp', true)

puts "Created #{proj_path}"
