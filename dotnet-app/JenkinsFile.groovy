def gitCommit = 'None Listed'
def gitBranch = 'main'
def configuration = 'Release'
def version = ''
def failBuild = false

pipeline {
    //agent {label 'P57'}
    agent any
    environment {
        major='1'
        minor='0'
        patch='0'
    }
    stages {
        stage('GIT Commit') {
            steps {
                echo 'Pulling...' + env.GIT_BRANCH
                script {
                    gitCommit = sh(returnStdout: true, script: "git log -1 --pretty=%b").trim()
                    echo "Commit Information: ${gitCommit}"
                }
                script {
                    gitBranch = env.GIT_BRANCH
                    gitBranch = gitBranch.replaceAll("/", "-")
                    if (gitBranch.contains('main')){
                        version = "${major}.${minor}.${patch}.${BUILD_NUMBER}"
                    }
                    else{
                        configuration = 'Debug'
                        version = "${major}.${minor}.${patch}-dev.${BUILD_NUMBER}"
                    }

                    echo 'Configuration is: ' + configuration
                    echo 'Git Branch is: ' + gitBranch
                    echo 'Version number is: ' + version
                }
                sh 'dotnet restore "dotnet-app\\ChatWithData.Web.csproj" --ignore-failed-sources --configfile "C:\\GlobalExecutables\\NuGet.config"'
            }
        }                           
        stage('Build Api') {
            steps {
                script {
                    dir("ChatWithData.Web\\\\bin\\\\${configuration}\\\\net8.0\\\\win-x64\\\\publish") {
                        deleteDir();
                    }
                }
                sh 'dotnet restore "dotnet-app\\ChatWithData.Web.csproj"'
                sh "dotnet publish dotnet-app\\\\ChatWithData.Web.csproj --configuration ${configuration} //p:PublishProfile=FolderProfile"
                sh "C:\\\\GlobalExecutables\\\\Octo.exe pack --id=ChatWithData.Web --version=${version} --basePath=dotnet-app\\\\bin\\\\${configuration}\\\\net8.0\\\\publish --releaseNotes=\"$gitCommit\""
            }
        }
        stage('Archive Artifacts and Push to Octopus') {
          steps {
            archiveArtifacts 'ChatWithData.Web.' + version + '.nupkg'
            withCredentials([string(credentialsId: 'OctopusAPIKey', variable: 'APIKey')]) {
                // CORE
                sh 'nuget push -noninteractive "ChatWithData.Web.' + version + '.nupkg" -Source https://mccioctopus.ga.com/nuget/packages/ -apiKey ${APIKey}'
            }
          }
        }     
        stage('SONAR SCAN API') {
            environment{
                def sqScannerMsBuildHome = tool 'SONARSCAN_MS_BUILD_NET46'
            }
            options {
                timestamps()
            }
            steps {
                withSonarQubeEnv('sonarqubedev') {
                    sh "${sqScannerMsBuildHome}\\\\SonarScanner.MSBuild.exe begin -key:ChatWithData.Web -v:${version}"
                    sh "dotnet build dotnet-app\\\\ChatWithData.Web.csproj"
                    sh "${sqScannerMsBuildHome}\\\\SonarScanner.MSBuild.exe end"
                }                
            }
        }            
    }
	post {
		failure {
			emailext (body: '''$PROJECT_NAME - Build # $BUILD_NUMBER - $BUILD_STATUS: Check console output at $BUILD_URL to view the results.''', subject: 'Jenkins $PROJECT_NAME - Build # $BUILD_NUMBER - $BUILD_STATUS!', to: 'DL-MCCI-DevOps@ga.com')
		}
	}
}
